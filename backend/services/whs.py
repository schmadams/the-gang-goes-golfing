# target path: backend/services/whs.py (new file)
"""
World Handicap System (WHS) Handicap Index calculation -- Rules of
Handicapping, USGA/R&A, effective January 2024. Implements:

  - Rule 3.1: Adjustment of Hole Scores (Net Double Bogey / Par+5 cap for
    a player without an established Handicap Index yet)
  - Rule 5.1: Score Differential = (113 / Slope) x (AGS - Course Rating - PCC)
  - Rule 5.2a: Handicap Index from a scoring record of 3-19 scores
  - Rule 5.2b: Handicap Index from 20+ scores (average of lowest 8 of the
    most recent 20)
  - Rule 5.3: Handicap Index capped at 54.0
  - Rule 5.8: Soft cap / hard cap on year-over-year increases, relative to
    the Low Handicap Index (lowest Handicap Index in the preceding 12
    months, not established until a player reaches 20 scores)
  - Rule 5.9: Exceptional Score Reduction

Sources for the exact figures below (fetched live, not from training
memory, since precision matters for something called "correct WHS"):
  - https://www.usga.org/handicapping/roh/Content/rules/5%202a%20For%20Fewer%20Than%2020%20Scores.htm
    (Rule 5.2a table)
  - https://www.usga.org/content/usga/home-page/handicapping/world-handicap-system/topics/soft-cap-hard-cap.html
    (soft cap +3.0/50%, hard cap +5.0, Low HI needs 20 scores)
  - USGA FAQ on Exceptional Score Reduction (-1 for 7.0-9.9 strokes
    better than Handicap Index, -2 for 10.0+, applied to every
    differential in the most-recent-20 scoring record)
  - USGA/Rules of Handicapping guidance on Net Double Bogey (par + 2 +
    handicap strokes) vs. the par + 5 cap used before a Handicap Index
    exists

Deliberately NOT implemented:

  - Playing Conditions Calculation (Rule 5.6) -- always treated as 0.
    PCC is a statistical adjustment computed from *all* players' scores
    on one course on one specific day, and needs at least 8 qualifying
    scores on that exact course that exact day before it applies at all.
    A small friends' app will realistically never clear that bar, so this
    always lands on the same "insufficient data -> PCC = 0" branch the
    real spec falls back to anyway.
  - 9-hole score differentials -- this app has no 9-hole round type,
    every completed round here is 18 holes.
  - Handicap Committee discretionary adjustments (Rule 5.2a clarifications
    5.2a/1, 5.2a/2) -- those are human judgment calls, not a formula.
"""
from dataclasses import dataclass
from datetime import date, timedelta

MAX_HANDICAP_INDEX = 54.0
MIN_SCORES_FOR_INDEX = 3
LOW_HI_SCORES_THRESHOLD = 20  # scores needed before a Low HI (and soft/hard cap) exists
LOW_HI_WINDOW_DAYS = 365
SOFT_CAP_THRESHOLD = 3.0
HARD_CAP_THRESHOLD = 5.0
SOFT_CAP_DAMPENING = 0.5

# Rule 5.2a -- for a scoring record of 3-19 differentials:
# {count: (how many of the lowest differentials to average, adjustment)}
_FEWER_THAN_20_TABLE = {
    3: (1, -2.0),
    4: (1, -1.0),
    5: (1, 0.0),
    6: (2, -1.0),
    7: (2, 0.0),
    8: (2, 0.0),
    9: (3, 0.0),
    10: (3, 0.0),
    11: (3, 0.0),
    12: (4, 0.0),
    13: (4, 0.0),
    14: (4, 0.0),
    15: (5, 0.0),
    16: (5, 0.0),
    17: (6, 0.0),
    18: (6, 0.0),
    19: (7, 0.0),
}


@dataclass
class HoleInput:
    par: int
    stroke_index: int
    strokes: int


@dataclass
class RoundInput:
    """One completed 18-hole round's worth of data, in the shape the WHS
    engine needs. Gathering this from the DB is _gather_round_inputs's
    job, not this module's core logic -- everything below this point up
    to the "DB orchestration" section is pure and has no idea Supabase
    exists. round_id is optional and unused by the calculation itself --
    it's only carried through so the breakdown functions below can tell
    the frontend which actual round each contributing differential came
    from."""
    played_on: date
    course_rating: float
    slope_rating: int
    holes: list[HoleInput]
    round_id: str | None = None


def _strokes_received(stroke_index: int, course_handicap_value: int) -> int:
    """
    Spreads a (whole-number) Course Handicap across the 18 holes in
    stroke-index order -- SI 1 (hardest) gets the first extra stroke, SI 2
    the second, and so on. A handicap over 18 wraps around (every hole
    gets one stroke, then the hardest holes get a second). A course
    handicap of 0 or below (scratch or "plus" players) works in reverse --
    strokes are taken away starting from the *easiest* hole (highest SI)
    instead of added. That reverse direction is the standard convention,
    but is genuinely untested here since it needs a scratch-or-better
    player to ever trigger -- worth a closer look if a plus-handicap
    player's numbers ever look off.
    """
    if course_handicap_value >= 0:
        base, extra_on = divmod(course_handicap_value, 18)
        return base + (1 if stroke_index <= extra_on else 0)

    magnitude = -course_handicap_value
    base, extra_on = divmod(magnitude, 18)
    reverse_index = 19 - stroke_index
    return -(base + (1 if reverse_index <= extra_on else 0))


def course_handicap(handicap_index: float, slope_rating: int, course_rating: float, par: int) -> int:
    """Rule 6.1. Rounded to the nearest whole number, half rounds away
    from zero (matches how every published Course Handicap table/app
    rounds 0.5, rather than Python's round-half-to-even default)."""
    raw = handicap_index * (slope_rating / 113) + (course_rating - par)
    return int(raw + 0.5) if raw >= 0 else -int(-raw + 0.5)


def adjusted_gross_score(round_input: RoundInput, prior_handicap_index: float | None) -> int:
    """
    Rule 3. Caps every hole at Net Double Bogey (par + 2 + handicap
    strokes received there) once the player has an established Handicap
    Index, or at a flat par + 5 while they're still establishing their
    first one (prior_handicap_index is None) -- Rule 3.1's allowance for
    a player without a Handicap Index yet.
    """
    par_total = sum(h.par for h in round_input.holes)

    if prior_handicap_index is None:
        caps = {h.stroke_index: h.par + 5 for h in round_input.holes}
    else:
        ch = course_handicap(prior_handicap_index, round_input.slope_rating, round_input.course_rating, par_total)
        caps = {
            h.stroke_index: h.par + 2 + _strokes_received(h.stroke_index, ch)
            for h in round_input.holes
        }

    return sum(min(h.strokes, caps[h.stroke_index]) for h in round_input.holes)


def score_differential(round_input: RoundInput, prior_handicap_index: float | None) -> float:
    """Rule 5.1. PCC is always 0 -- see module docstring."""
    ags = adjusted_gross_score(round_input, prior_handicap_index)
    raw = (113 / round_input.slope_rating) * (ags - round_input.course_rating)
    return round(raw, 1)


def _handicap_index_from_scoring_record(differentials: list[float]) -> float | None:
    """
    Rule 5.2. `differentials` is a player's most recent (already
    ESR-adjusted) Score Differentials, capped by the caller at the most
    recent 20. Returns None if there aren't at least 3 yet -- WHS doesn't
    establish a Handicap Index before that.
    """
    count = len(differentials)
    if count < MIN_SCORES_FOR_INDEX:
        return None

    if count >= LOW_HI_SCORES_THRESHOLD:
        num_to_average, adjustment = 8, 0.0
    else:
        num_to_average, adjustment = _FEWER_THAN_20_TABLE[count]

    lowest = sorted(differentials)[:num_to_average]
    index = sum(lowest) / len(lowest) + adjustment
    return round(min(index, MAX_HANDICAP_INDEX), 1)


def _num_counting(scoring_record_size: int) -> int:
    """How many of the current scoring record's differentials are
    actually being averaged into the Handicap Index right now -- the
    "lowest N" from the Rule 5.2a table below 20 scores, or the lowest 8
    of the most recent 20 once established. 0 below 3 scores, matching
    _handicap_index_from_scoring_record's own minimum."""
    if scoring_record_size >= LOW_HI_SCORES_THRESHOLD:
        return 8
    return _FEWER_THAN_20_TABLE.get(scoring_record_size, (0, 0.0))[0]


def _simulate(rounds_chronological: list[RoundInput]) -> tuple[float | None, list[dict]]:
    """
    The actual step-by-step WHS algorithm, oldest round to newest. Shared
    by calculate_handicap_index (which only needs the final number) and
    get_handicap_breakdown (which also needs to know which specific
    rounds are in the current scoring record). Returns
    (final_handicap_index, final_scoring_record) -- each scoring_record
    entry is a dict with round_id/played_on/gross_score/
    adjusted_gross_score/slope_rating/course_rating/differential.

    This has to be a simulation rather than one pass over the final 20
    differentials, because two rules depend on state *at the time each
    round was played*, not on the final scoring record:

      - Net Double Bogey (via adjusted_gross_score) needs the Handicap
        Index that was in effect *before* that round, not the final one.
      - Exceptional Score Reduction (Rule 5.9) compares each round's
        differential to the Handicap Index in effect when it was played,
        and -- when triggered -- retroactively adjusts every differential
        currently in the scoring record.

    Soft cap / hard cap (Rule 5.8) apply on top of the result once a Low
    Handicap Index exists -- the lowest *established* Handicap Index (one
    calculated from 20+ scores) in the preceding 12 months.
    """
    scoring_record: list[dict] = []  # up to 20 entries, oldest first
    handicap_index: float | None = None
    # (date, index) pairs -- only appended once the scoring record has
    # reached 20, since a Low HI "is not established" before that.
    established_index_history: list[tuple[date, float]] = []

    for round_input in rounds_chronological:
        ags = adjusted_gross_score(round_input, handicap_index)
        gross = sum(h.strokes for h in round_input.holes)
        raw_diff = round((113 / round_input.slope_rating) * (ags - round_input.course_rating), 1)

        # Exceptional Score Reduction -- compares the new round to the
        # Handicap Index in effect *before* it's added, and retroactively
        # nudges every differential currently in the scoring record.
        if handicap_index is not None:
            better_by = handicap_index - raw_diff
            if better_by >= 10.0:
                for entry in scoring_record:
                    entry["differential"] = round(entry["differential"] - 2.0, 1)
            elif better_by >= 7.0:
                for entry in scoring_record:
                    entry["differential"] = round(entry["differential"] - 1.0, 1)

        scoring_record.append({
            "round_id": round_input.round_id,
            "played_on": round_input.played_on,
            "gross_score": gross,
            "adjusted_gross_score": ags,
            "slope_rating": round_input.slope_rating,
            "course_rating": round_input.course_rating,
            "differential": raw_diff,
        })
        if len(scoring_record) > LOW_HI_SCORES_THRESHOLD:
            scoring_record = scoring_record[-LOW_HI_SCORES_THRESHOLD:]

        new_index = _handicap_index_from_scoring_record([e["differential"] for e in scoring_record])

        if new_index is not None and len(scoring_record) >= LOW_HI_SCORES_THRESHOLD:
            window_start = round_input.played_on - timedelta(days=LOW_HI_WINDOW_DAYS)
            recent_low = min(
                (hi for d, hi in established_index_history if d >= window_start),
                default=None,
            )
            if recent_low is not None:
                increase = new_index - recent_low
                if increase > SOFT_CAP_THRESHOLD:
                    dampened = SOFT_CAP_THRESHOLD + (increase - SOFT_CAP_THRESHOLD) * SOFT_CAP_DAMPENING
                    new_index = round(recent_low + min(dampened, HARD_CAP_THRESHOLD), 1)
            established_index_history.append((round_input.played_on, new_index))

        handicap_index = new_index

    return handicap_index, scoring_record


def calculate_handicap_index(rounds_chronological: list[RoundInput]) -> float | None:
    """Runs the full WHS algorithm over a player's entire completed-round
    history and returns just their current Handicap Index -- None if they
    don't have at least 3 qualifying scores yet. See _simulate for how."""
    handicap_index, _ = _simulate(rounds_chronological)
    return handicap_index


def get_handicap_breakdown(rounds_chronological: list[RoundInput]) -> dict:
    """
    Same simulation as calculate_handicap_index, but returns the full
    scoring record too -- every round currently within the most-recent-20
    window, each tagged with whether it's one of the "lowest N" actually
    being averaged into the Handicap Index right now. This is what powers
    the home page's "Contributing Rounds" view; most recent round first.
    """
    handicap_index, scoring_record = _simulate(rounds_chronological)

    num_counting = _num_counting(len(scoring_record))
    # Ties broken by recency (the more recent of two equal differentials
    # counts) purely for a stable, deterministic display -- doesn't affect
    # the Handicap Index number itself either way, only which rows get
    # highlighted as "counting".
    ranked = sorted(range(len(scoring_record)), key=lambda i: (scoring_record[i]["differential"], -i))
    counting_indexes = set(ranked[:num_counting])

    rounds_out = [
        {**entry, "counting": i in counting_indexes}
        for i, entry in enumerate(scoring_record)
    ]
    rounds_out.reverse()  # most recent first, for display

    return {"handicap_index": handicap_index, "rounds": rounds_out}


# ── DB orchestration ──────────────────────────────────────────────

def _gather_round_inputs(player_id: str) -> list[RoundInput]:
    """
    Pulls every completed round this player belongs to (owner or accepted
    participant), oldest to newest, and hydrates each into a RoundInput --
    skipping any round that can't produce a valid differential (no
    rating/slope on its tee, or an incomplete scorecard). Shared by
    recalculate_and_store_handicap (writes the result) and
    get_player_handicap_breakdown (just reads it) so the two can never
    disagree about which rounds are eligible.
    """
    from backend.database import supabase

    rp_response = (
        supabase.table("round_players")
        .select("round_id")
        .eq("player_id", player_id)
        .eq("status", "accepted")
        .execute()
    )
    round_ids = [r["round_id"] for r in (rp_response.data or [])]
    if not round_ids:
        return []

    rounds_response = (
        supabase.table("rounds")
        .select("id, tee_id, completed_at")
        .in_("id", round_ids)
        .eq("status", "completed")
        .order("completed_at")
        .execute()
    )
    completed_rounds = rounds_response.data or []

    # Batched instead of 3 queries *per round* (tee, its holes, this
    # player's scores) -- with a handful of completed rounds that was
    # already 15+ sequential round trips, and the handicap breakdown is
    # recomputed on every home page load, so it was the single slowest
    # thing on that page. Same fix as rounds.py's
    # _batch_fetch_round_hydration for the same reason.
    completed_round_ids = [r["id"] for r in completed_rounds]
    tee_ids = list({r["tee_id"] for r in completed_rounds if r.get("tee_id")})

    tee_by_id: dict[str, dict] = {}
    holes_by_tee_id: dict[str, dict[int, dict]] = {}
    if tee_ids:
        tees_response = (
            supabase.table("course_tees")
            .select("id, course_rating, slope_rating")
            .in_("id", tee_ids)
            .execute()
        )
        tee_by_id = {t["id"]: t for t in (tees_response.data or [])}

        holes_response = (
            supabase.table("course_holes")
            .select("tee_id, hole_number, par, stroke_index")
            .in_("tee_id", tee_ids)
            .execute()
        )
        for hole in (holes_response.data or []):
            holes_by_tee_id.setdefault(hole["tee_id"], {})[hole["hole_number"]] = hole

    scores_by_round_id: dict[str, dict[int, dict]] = {}
    if completed_round_ids:
        scores_response = (
            supabase.table("round_scores")
            .select("round_id, hole_number, strokes")
            .in_("round_id", completed_round_ids)
            .eq("player_id", player_id)
            .execute()
        )
        for score in (scores_response.data or []):
            scores_by_round_id.setdefault(score["round_id"], {})[score["hole_number"]] = score

    round_inputs: list[RoundInput] = []
    for round_row in completed_rounds:
        tee_id = round_row.get("tee_id")
        if not tee_id:
            continue

        tee = tee_by_id.get(tee_id)
        if not tee or tee.get("course_rating") is None or tee.get("slope_rating") is None:
            # No rating/slope for this tee -- e.g. a manually-entered
            # round where the owner skipped the optional rating fields,
            # or a course imported before those fields were ever asked
            # for. This round just doesn't contribute a differential.
            continue

        holes_by_number = holes_by_tee_id.get(tee_id, {})
        strokes_by_number = {
            n: s.get("strokes") for n, s in scores_by_round_id.get(round_row["id"], {}).items()
        }

        holes: list[HoleInput] = []
        complete = True
        for n in range(1, 19):
            hole = holes_by_number.get(n)
            strokes = strokes_by_number.get(n)
            if not hole or hole.get("par") is None or hole.get("stroke_index") is None or strokes is None:
                complete = False
                break
            holes.append(HoleInput(par=hole["par"], stroke_index=hole["stroke_index"], strokes=strokes))

        if not complete:
            # An incomplete scorecard can't produce a valid Adjusted Gross
            # Score -- skip it rather than guess at the missing holes.
            continue

        completed_at = round_row.get("completed_at")
        played_on = date.fromisoformat(completed_at[:10]) if completed_at else date.today()

        round_inputs.append(RoundInput(
            round_id=round_row["id"],
            played_on=played_on,
            course_rating=tee["course_rating"],
            slope_rating=tee["slope_rating"],
            holes=holes,
        ))

    return round_inputs


def recalculate_and_store_handicap(player_id: str) -> dict | None:
    """
    Rebuilds a player's full Handicap Index from their entire completed-
    round history and, if it's different from what's currently on file
    (or this is their first-ever result), inserts a new dated row into
    player_handicaps -- what every other "current handicap" display in
    the app already reads from.

    Called after every round finish (see finish_round in rounds.py). Safe
    to call any time since it always recomputes from scratch rather than
    incrementally patching a stored value -- there's no risk of drifting
    from what the full history actually says.
    """
    # Local import -- avoids a circular import with backend.services.rounds,
    # which needs to call *this* module from finish_round.
    from backend.database import supabase
    from backend.services.handicaps import get_current_player_handicap

    round_inputs = _gather_round_inputs(player_id)
    if not round_inputs:
        return None

    new_index = calculate_handicap_index(round_inputs)
    if new_index is None:
        return None

    current = get_current_player_handicap(player_id)
    if current is not None and current.get("handicap") == new_index:
        return current

    response = (
        supabase.table("player_handicaps")
        .insert({"player_id": player_id, "handicap": new_index})
        .execute()
    )
    return response.data[0] if response.data else None


def get_player_handicap_breakdown(player_id: str) -> dict:
    """Read-only counterpart to recalculate_and_store_handicap -- same
    data, same calculation, but returns the full scoring-record breakdown
    instead of writing anything. Used by the home page's handicap panel."""
    round_inputs = _gather_round_inputs(player_id)
    if not round_inputs:
        return {"handicap_index": None, "rounds": []}
    return get_handicap_breakdown(round_inputs)