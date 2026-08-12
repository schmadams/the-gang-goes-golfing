# target path: backend/services/rounds.py (full replacement)
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from backend.database import supabase
from backend.services.friends import list_friends
from backend.services.handicaps import get_current_player_handicap

_EXPIRY_HOURS = 8  # rounds still in_progress after this long are auto-scrapped


class RoundAlreadyActiveError(Exception):
    """Raised when a player tries to start a round (or accept an invite
    into one) while already an accepted participant -- owner or joined --
    in another in-progress round."""


class ManualScorecardValidationError(Exception):
    """Raised at finish time if a manual round's hole data is incomplete
    or its stroke indexes aren't a clean 1-18 permutation."""


class TooManyInvitesError(Exception):
    """Raised if more than 3 friends are invited into a round."""


class NotFriendsError(Exception):
    """Raised if a round invite is sent to someone who isn't a confirmed
    friend of the round's owner."""


class RoundInviteNotFoundError(Exception):
    """Raised when responding to a round invite that doesn't exist, or has
    already been responded to."""


@contextmanager
def _timed(label: str, source: str = "database"):
    """Same pattern as backend/services/courses.py -- logs elapsed time so
    slowness can be traced to a specific query from the console alone."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[TIMING] {source:12s} {elapsed_ms:8.1f}ms  {label}")


def _hole_handicap_strokes(handicap: float | None, stroke_index: int | None) -> int:
    """
    Standard golf handicap stroke allocation: a player's (rounded) course
    handicap is spread across the 18 holes in stroke-index order -- the
    hardest hole (SI 1) gets an extra stroke first, then SI 2, and so on.
    A handicap over 18 wraps around (every hole gets at least one stroke,
    then the hardest holes get a second). A negative "plus" handicap
    (better than scratch) works the same way in reverse -- strokes are
    given back on the hardest holes instead of added.
    """
    if handicap is None or stroke_index is None:
        return 0

    rounded = round(handicap)
    sign = 1 if rounded >= 0 else -1
    magnitude = abs(rounded)

    base, extra_on = divmod(magnitude, 18)
    strokes = base + (1 if stroke_index <= extra_on else 0)
    return sign * strokes


def _stableford_points(net_strokes: int | None, par: int | None) -> int | None:
    """Standard Stableford scoring off net score: 2 points for net par,
    +1 per stroke better, -1 per stroke worse, floored at 0."""
    if net_strokes is None or par is None:
        return None
    return max(0, 2 - (net_strokes - par))


def _round_putts_and_fairway_pct(holes: list[dict]) -> tuple[int | None, float | None]:
    """Per-round totals used by the Player Analysis charts: total putts
    across holes that have a putts value, and fairway-hit % across
    non-par-3 holes that have a fairway_hit value (par 3s are never
    eligible -- see live_round.py)."""
    putts_values = [h["putts"] for h in holes if h.get("putts") is not None]
    putts_total = sum(putts_values) if putts_values else None

    eligible = [h for h in holes if h.get("par") != 3 and h.get("fairway_hit") is not None]
    fairway_pct = round(100 * sum(1 for h in eligible if h["fairway_hit"]) / len(eligible), 1) if eligible else None

    return putts_total, fairway_pct


def _expire_stale_rounds() -> None:
    """Lazily deletes any round that's been in_progress for more than
    _EXPIRY_HOURS -- there's no cron/scheduler in this app, so this is
    called at the top of the read/write paths that care whether a round is
    "really" still active (active-round lookups, starting a round,
    accepting an invite), and it self-heals from there rather than needing
    real infrastructure."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_EXPIRY_HOURS)).isoformat()
    with _timed(f"find rounds in_progress since before {cutoff}"):
        response = (
            supabase
            .table("rounds")
            .select("id")
            .eq("status", "in_progress")
            .lt("started_at", cutoff)
            .execute()
        )
    for row in (response.data or []):
        delete_round(row["id"])


def _get_active_round_id_for_player(player_id: str) -> str | None:
    """The one round (if any) this player is currently an accepted
    participant in -- owner or joined, doesn't matter which. This is what
    the "one active round per player" rule now actually checks, since a
    player can be tied up in someone else's round without owning it."""
    _expire_stale_rounds()

    with _timed(f"select accepted round_players for player {player_id}"):
        rp_response = (
            supabase
            .table("round_players")
            .select("round_id")
            .eq("player_id", player_id)
            .eq("status", "accepted")
            .execute()
        )
    round_ids = [r["round_id"] for r in (rp_response.data or [])]
    if not round_ids:
        return None

    with _timed(f"select in_progress round among player {player_id}'s rounds"):
        rounds_response = (
            supabase
            .table("rounds")
            .select("id")
            .in_("id", round_ids)
            .eq("status", "in_progress")
            .limit(1)
            .execute()
        )
    rows = rounds_response.data or []
    return rows[0]["id"] if rows else None


def _hydrate_round(round_row: dict) -> dict:
    """
    Attaches every accepted participant's 18-hole scorecard (plus display
    info) to a bare rounds row -- one entry per participant in `players`,
    invited-but-not-yet-responded players in `pending_invites` instead
    (they don't have a scorecard yet). For a non-manual round (or a manual
    one that's already been finished, so tee_id is set), par/yardage/
    stroke_index come from the real course_holes rows so the frontend
    always has one consistent shape to render regardless of source.
    """
    round_id = round_row["id"]

    with _timed(f"fetch round_players for round {round_id}"):
        rp_response = (
            supabase
            .table("round_players")
            .select("*, players(id, first_name, surname, nickname)")
            .eq("round_id", round_id)
            .order("invited_at")
            .execute()
        )
    round_player_rows = rp_response.data or []

    club_name = round_row.get("manual_club_name")
    course_name = None
    tee_name = round_row.get("manual_tee_name")
    course_holes_by_number = {}

    if round_row.get("tee_id"):
        with _timed(f"fetch tee {round_row['tee_id']} (+course) for round {round_id}"):
            tee_response = (
                supabase
                .table("course_tees")
                .select("*, courses(club_name, course_name)")
                .eq("id", round_row["tee_id"])
                .maybe_single()
                .execute()
            )
        tee = tee_response.data if tee_response is not None else None

        if tee:
            tee_name = tee["name"]
            course_info = tee.get("courses") or {}
            club_name = course_info.get("club_name") or club_name
            course_name = course_info.get("course_name")

            with _timed(f"fetch course_holes for tee {tee['id']}"):
                holes_response = (
                    supabase
                    .table("course_holes")
                    .select("*")
                    .eq("tee_id", tee["id"])
                    .order("hole_number")
                    .execute()
                )
            course_holes_by_number = {
                h["hole_number"]: h for h in (holes_response.data or [])
            }

    with _timed(f"fetch round_scores for round {round_id}"):
        scores_response = (
            supabase
            .table("round_scores")
            .select("*")
            .eq("round_id", round_id)
            .execute()
        )
    scores_by_player_hole: dict[str, dict[int, dict]] = {}
    for score in (scores_response.data or []):
        scores_by_player_hole.setdefault(score["player_id"], {})[score["hole_number"]] = score

    players = []
    pending_invites = []
    for rp in round_player_rows:
        player_info = rp.get("players") or {}
        entry = {
            "player_id": rp["player_id"],
            "is_owner": rp["is_owner"],
            "status": rp["status"],
            "first_name": player_info.get("first_name"),
            "surname": player_info.get("surname"),
            "nickname": player_info.get("nickname"),
        }

        if rp["status"] != "accepted":
            entry["holes"] = []
            pending_invites.append(entry)
            continue

        player_scores = scores_by_player_hole.get(rp["player_id"], {})
        holes = []
        for hole_number in range(1, 19):
            score = player_scores.get(hole_number, {})
            course_hole = course_holes_by_number.get(hole_number, {})
            holes.append({
                "hole_number": hole_number,
                "strokes": score.get("strokes"),
                "putts": score.get("putts"),
                "fairway_hit": score.get("fairway_hit"),
                "manual_par": score.get("manual_par"),
                "manual_yardage": score.get("manual_yardage"),
                "manual_stroke_index": score.get("manual_stroke_index"),
                "par": course_hole.get("par"),
                "yardage": course_hole.get("yardage"),
                "stroke_index": course_hole.get("stroke_index"),
            })
        entry["holes"] = holes
        players.append(entry)

    return {
        **round_row,
        "club_name": club_name,
        "course_name": course_name,
        "tee_name": tee_name,
        "players": players,
        "pending_invites": pending_invites,
    }


def get_active_round(player_id: str) -> dict | None:
    round_id = _get_active_round_id_for_player(player_id)
    if not round_id:
        return None
    return get_round(round_id, viewer_player_id=player_id)


def get_round(round_id: str, viewer_player_id: str | None = None) -> dict | None:
    with _timed(f"select round {round_id}"):
        response = supabase.table("rounds").select("*").eq("id", round_id).maybe_single().execute()
    round_row = response.data if response is not None else None
    if not round_row:
        return None

    hydrated = _hydrate_round(round_row)
    if viewer_player_id is not None:
        hydrated["is_owner"] = round_row["player_id"] == viewer_player_id
    return hydrated


def list_pending_round_invites(player_id: str) -> list[dict]:
    """Rounds this player has been invited to but hasn't responded to yet
    -- only surfaces invites into rounds that are still actually
    in_progress (an invite into a round that's since been scrapped or
    expired isn't worth showing)."""
    with _timed(f"select pending round invites for player {player_id}"):
        response = (
            supabase
            .table("round_players")
            .select("*, rounds(*)")
            .eq("player_id", player_id)
            .eq("status", "invited")
            .execute()
        )
    rows = response.data or []

    invites = []
    for row in rows:
        round_row = row.get("rounds") or {}
        if round_row.get("status") != "in_progress":
            continue

        owner_first_name = None
        owner_surname = None
        club_name = round_row.get("manual_club_name")
        course_name = None

        owner_id = round_row.get("player_id")
        if owner_id:
            with _timed(f"fetch owner {owner_id} for invite display"):
                owner_response = (
                    supabase
                    .table("players")
                    .select("first_name, surname")
                    .eq("id", owner_id)
                    .maybe_single()
                    .execute()
                )
            owner = owner_response.data if owner_response is not None else None
            if owner:
                owner_first_name = owner.get("first_name")
                owner_surname = owner.get("surname")

        if round_row.get("tee_id"):
            with _timed(f"fetch tee {round_row['tee_id']} for invite display"):
                tee_response = (
                    supabase
                    .table("course_tees")
                    .select("*, courses(club_name, course_name)")
                    .eq("id", round_row["tee_id"])
                    .maybe_single()
                    .execute()
                )
            tee = tee_response.data if tee_response is not None else None
            if tee:
                course_info = tee.get("courses") or {}
                club_name = course_info.get("club_name") or club_name
                course_name = course_info.get("course_name")

        invites.append({
            "round_id": row["round_id"],
            "player_id": row["player_id"],
            "is_owner": row["is_owner"],
            "status": row["status"],
            "invited_at": row["invited_at"],
            "owner_first_name": owner_first_name,
            "owner_surname": owner_surname,
            "club_name": club_name,
            "course_name": course_name,
        })
    return invites


def respond_to_round_invite(round_id: str, player_id: str, accept: bool) -> dict:
    with _timed(f"select round_player round={round_id} player={player_id}"):
        response = (
            supabase
            .table("round_players")
            .select("*")
            .eq("round_id", round_id)
            .eq("player_id", player_id)
            .maybe_single()
            .execute()
        )
    row = response.data if response is not None else None
    if not row or row["status"] != "invited":
        raise RoundInviteNotFoundError("No pending invite found for that round.")

    if accept and _get_active_round_id_for_player(player_id):
        raise RoundAlreadyActiveError(
            "Finish or scrap your current live round before accepting this invite."
        )

    now = datetime.now(timezone.utc).isoformat()
    new_status = "accepted" if accept else "declined"
    with _timed(f"update round_players round={round_id} player={player_id}"):
        supabase.table("round_players").update(
            {"status": new_status, "responded_at": now}
        ).eq("round_id", round_id).eq("player_id", player_id).execute()

    if accept:
        placeholder_scores = [
            {"round_id": round_id, "player_id": player_id, "hole_number": n} for n in range(1, 19)
        ]
        with _timed(f"insert 18 round_scores placeholders for player={player_id}, round={round_id}"):
            supabase.table("round_scores").insert(placeholder_scores).execute()

    return get_round(round_id, viewer_player_id=player_id)


def _build_round_summary(round_row: dict, player_id: str, handicap: float | None) -> dict:
    """
    Shared by list_player_rounds (Rounds History / Scoring History) and
    get_player_analysis (Player Analysis charts) -- both need the same
    per-hole hydration (course/tee names, par/stroke index from
    course_holes, and handicap-adjusted net strokes / Stableford points
    per hole) for one specific player's own scorecard within a round they
    belong to (owner or accepted participant), not everyone's.
    """
    club_name = round_row.get("manual_club_name")
    course_name = None
    tee_name = round_row.get("manual_tee_name")
    course_holes_by_number = {}

    if round_row.get("tee_id"):
        with _timed(f"fetch tee {round_row['tee_id']} (+course) for round {round_row['id']}"):
            tee_response = (
                supabase
                .table("course_tees")
                .select("*, courses(club_name, course_name)")
                .eq("id", round_row["tee_id"])
                .maybe_single()
                .execute()
            )
        tee = tee_response.data if tee_response is not None else None
        if tee:
            tee_name = tee["name"]
            course_info = tee.get("courses") or {}
            club_name = course_info.get("club_name") or club_name
            course_name = course_info.get("course_name")

            with _timed(f"fetch course_holes for tee {tee['id']}"):
                holes_response = (
                    supabase
                    .table("course_holes")
                    .select("*")
                    .eq("tee_id", tee["id"])
                    .order("hole_number")
                    .execute()
                )
            course_holes_by_number = {
                h["hole_number"]: h for h in (holes_response.data or [])
            }

    with _timed(f"fetch round_scores for round {round_row['id']} player {player_id}"):
        scores_response = (
            supabase
            .table("round_scores")
            .select("hole_number, strokes, putts, fairway_hit")
            .eq("round_id", round_row["id"])
            .eq("player_id", player_id)
            .order("hole_number")
            .execute()
        )
    scores_by_hole = {s["hole_number"]: s for s in (scores_response.data or [])}

    holes = []
    for hole_number in range(1, 19):
        course_hole = course_holes_by_number.get(hole_number, {})
        score = scores_by_hole.get(hole_number, {})
        par = course_hole.get("par")
        stroke_index = course_hole.get("stroke_index")
        strokes = score.get("strokes")

        hcp_strokes = _hole_handicap_strokes(handicap, stroke_index)
        net_strokes = strokes - hcp_strokes if strokes is not None else None
        stableford_points = _stableford_points(net_strokes, par)

        holes.append({
            "hole_number": hole_number,
            "par": par,
            "stroke_index": stroke_index,
            "strokes": strokes,
            "putts": score.get("putts"),
            "fairway_hit": score.get("fairway_hit"),
            "net_strokes": net_strokes,
            "stableford_points": stableford_points,
        })

    strokes_list = [h["strokes"] for h in holes if h["strokes"] is not None]
    stableford_list = [h["stableford_points"] for h in holes if h["stableford_points"] is not None]

    return {
        **round_row,
        "club_name": club_name,
        "course_name": course_name,
        "tee_name": tee_name,
        "total_strokes": sum(strokes_list) if strokes_list else None,
        "holes_played": len(strokes_list),
        "holes": holes,
        "handicap": handicap,
        "total_stableford": sum(stableford_list) if stableford_list else None,
    }


def list_player_rounds(player_id: str, limit: int = 20) -> list[dict]:
    """
    List for the Rounds History panel (compact) and Scoring History page
    (detailed) -- both read from this one function. Covers every round
    this player belongs to, as owner or accepted participant, not just
    ones they started. Includes any in-progress round (first in the list)
    alongside up to `limit` completed rounds -- the `status` field is what
    lets the frontend show a "live" marker instead of needing a separate
    lookup.
    """
    _expire_stale_rounds()

    with _timed(f"select accepted round_players for player {player_id}"):
        rp_response = (
            supabase
            .table("round_players")
            .select("round_id")
            .eq("player_id", player_id)
            .eq("status", "accepted")
            .execute()
        )
    round_ids = [r["round_id"] for r in (rp_response.data or [])]
    if not round_ids:
        return []

    with _timed(f"select in_progress rounds among player {player_id}'s rounds"):
        active_response = (
            supabase
            .table("rounds")
            .select("*")
            .in_("id", round_ids)
            .eq("status", "in_progress")
            .execute()
        )
    active_rounds = active_response.data or []

    with _timed(f"select completed rounds among player {player_id}'s rounds"):
        completed_response = (
            supabase
            .table("rounds")
            .select("*")
            .in_("id", round_ids)
            .eq("status", "completed")
            .order("completed_at", desc=True)
            .limit(limit)
            .execute()
        )
    completed_rounds = completed_response.data or []

    rounds = active_rounds + completed_rounds

    with _timed(f"fetch current handicap for player {player_id}"):
        handicap_row = get_current_player_handicap(player_id)
    handicap = handicap_row["handicap"] if handicap_row else None

    return [_build_round_summary(round_row, player_id, handicap) for round_row in rounds]


def get_player_analysis(player_id: str, window: int = 5) -> list[dict]:
    """
    Chronological (oldest -> newest) per-round putts total and fairway-hit
    % for the Player Analysis page, each with a trailing rolling average
    (default 5-round window), across every completed round this player
    belongs to (owner or accepted participant). The two rolling averages
    are computed over each stat's own series independently -- a round with
    no putts entered doesn't create a gap in the fairway line or shrink
    its window, and vice versa.
    """
    with _timed(f"select accepted round_players for player {player_id} (analysis)"):
        rp_response = (
            supabase
            .table("round_players")
            .select("round_id")
            .eq("player_id", player_id)
            .eq("status", "accepted")
            .execute()
        )
    round_ids = [r["round_id"] for r in (rp_response.data or [])]
    if not round_ids:
        return []

    with _timed(f"select completed rounds among player {player_id}'s rounds (analysis)"):
        response = (
            supabase
            .table("rounds")
            .select("*")
            .in_("id", round_ids)
            .eq("status", "completed")
            .order("completed_at", desc=False)
            .limit(200)
            .execute()
        )
    completed_rounds = response.data or []

    points = []
    for round_row in completed_rounds:
        summary = _build_round_summary(round_row, player_id, handicap=None)
        putts_total, fairway_pct = _round_putts_and_fairway_pct(summary["holes"])
        points.append({
            "date": (round_row.get("completed_at") or "")[:10],
            "putts_total": putts_total,
            "putts_rolling_avg": None,
            "fairway_pct": fairway_pct,
            "fairway_rolling_avg": None,
        })

    putts_series = [p for p in points if p["putts_total"] is not None]
    for i, p in enumerate(putts_series):
        window_vals = [x["putts_total"] for x in putts_series[max(0, i - window + 1):i + 1]]
        p["putts_rolling_avg"] = round(sum(window_vals) / len(window_vals), 1)

    fairway_series = [p for p in points if p["fairway_pct"] is not None]
    for i, p in enumerate(fairway_series):
        window_vals = [x["fairway_pct"] for x in fairway_series[max(0, i - window + 1):i + 1]]
        p["fairway_rolling_avg"] = round(sum(window_vals) / len(window_vals), 1)

    return points


def start_round(payload: dict) -> dict:
    """
    Starts a live round, optionally inviting up to 3 confirmed friends
    (they each get a round_players row with status='invited' and have to
    accept before their scorecard exists -- see respond_to_round_invite).
    Pre-checks for an existing active-round membership (fast, friendly
    rejection) but also relies on the DB's partial unique index
    (rounds_one_active_per_player) as the real guarantee under concurrent
    requests for the owner side specifically -- so a duplicate-key error
    from the insert itself is also caught and turned into the same
    friendly error.
    """
    player_id = payload["player_id"]
    invited_player_ids = payload.get("invited_player_ids") or []

    if len(invited_player_ids) > 3:
        raise TooManyInvitesError("You can only add up to 3 other friends to a round.")

    if invited_player_ids:
        friend_ids = {f["player_id"] for f in list_friends(player_id)}
        not_friends = [pid for pid in invited_player_ids if pid not in friend_ids]
        if not_friends:
            raise NotFriendsError("You can only invite confirmed friends to a round.")

    if _get_active_round_id_for_player(player_id):
        raise RoundAlreadyActiveError(
            "You already have a live round in progress. Finish it before starting a new one."
        )

    round_payload = {
        "player_id": player_id,
        "course_id": payload.get("course_id"),
        "tee_id": payload.get("tee_id"),
        "is_manual": payload.get("is_manual", False),
        "manual_club_name": payload.get("manual_club_name"),
        "manual_tee_name": payload.get("manual_tee_name"),
    }

    with _timed("insert rounds row"):
        try:
            round_row = supabase.table("rounds").insert(round_payload).execute().data[0]
        except Exception as exc:
            if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                raise RoundAlreadyActiveError(
                    "You already have a live round in progress. Finish it before starting a new one."
                )
            raise

    round_id = round_row["id"]
    now = datetime.now(timezone.utc).isoformat()

    round_player_rows = [
        {"round_id": round_id, "player_id": player_id, "is_owner": True, "status": "accepted", "responded_at": now}
    ] + [
        {"round_id": round_id, "player_id": pid, "is_owner": False, "status": "invited"}
        for pid in invited_player_ids
    ]
    with _timed(f"insert round_players for round {round_id}"):
        supabase.table("round_players").insert(round_player_rows).execute()

    placeholder_scores = [
        {"round_id": round_id, "player_id": player_id, "hole_number": n} for n in range(1, 19)
    ]
    with _timed(f"insert 18 round_scores placeholders for owner, round {round_id}"):
        supabase.table("round_scores").insert(placeholder_scores).execute()

    return get_round(round_id, viewer_player_id=player_id)


def update_hole_score(round_id: str, player_id: str, hole_number: int, updates: dict) -> dict | None:
    if not updates:
        return get_round(round_id, viewer_player_id=player_id)

    with _timed(f"update round_scores round={round_id} player={player_id} hole={hole_number}"):
        response = (
            supabase
            .table("round_scores")
            .update(updates)
            .eq("round_id", round_id)
            .eq("player_id", player_id)
            .eq("hole_number", hole_number)
            .execute()
        )

    if not response.data:
        return None

    return get_round(round_id, viewer_player_id=player_id)


def _create_course_from_manual_entry(round_data: dict, holes: list[dict]) -> None:
    """
    Validates the manually-entered scorecard is complete and internally
    consistent, then writes it as a real courses/course_tees/course_holes
    entry (same tables the UK Golf API import path uses) and links the
    round to it. This is what makes "upload the manual values to the DB"
    happen -- it runs once, at finish time, not per keystroke. `holes`
    comes from the round owner's scorecard specifically -- par/yardage/SI
    describe the course, not any one player's performance, so the owner's
    entries are treated as the authoritative source when multiple players
    are in the round.
    """
    missing = [
        h["hole_number"] for h in holes
        if h["manual_par"] is None or h["manual_yardage"] is None or h["manual_stroke_index"] is None
    ]
    if missing:
        raise ManualScorecardValidationError(
            "Enter par, length, and stroke index for every hole before finishing -- "
            f"missing on hole(s): {missing}"
        )

    stroke_indexes = [h["manual_stroke_index"] for h in holes]
    if sorted(stroke_indexes) != list(range(1, 19)):
        raise ManualScorecardValidationError(
            "Stroke indexes must cover 1-18 with no duplicates and none missing."
        )

    club_name = round_data.get("manual_club_name") or "Manually entered course"
    tee_name = round_data.get("manual_tee_name") or "Custom"
    course_id = round_data.get("course_id")

    if not course_id:
        with _timed("insert courses row from manual entry"):
            course_row = supabase.table("courses").insert({
                # courses.external_club_id is NOT NULL UNIQUE -- this course
                # didn't come from the UK Golf API, so there's no real
                # external id. A "manual-" prefixed UUID satisfies the
                # constraint without ever colliding with a real one, and
                # makes manually-entered courses easy to spot later if we
                # ever want to filter them out of search.
                "external_club_id": f"manual-{uuid.uuid4()}",
                "club_name": club_name,
                "course_name": None,
            }).execute().data[0]
        course_id = course_row["id"]

    total_par = sum(h["manual_par"] for h in holes)

    with _timed("insert course_tees row from manual entry"):
        tee_row = supabase.table("course_tees").insert({
            "course_id": course_id,
            "name": tee_name,
            "par": total_par,
        }).execute().data[0]

    hole_rows = [
        {
            "tee_id": tee_row["id"],
            "hole_number": h["hole_number"],
            "par": h["manual_par"],
            "yardage": h["manual_yardage"],
            "stroke_index": h["manual_stroke_index"],
        }
        for h in holes
    ]
    with _timed("insert course_holes rows from manual entry"):
        supabase.table("course_holes").insert(hole_rows).execute()

    with _timed(f"link round {round_data['id']} to newly created course/tee"):
        supabase.table("rounds").update({
            "course_id": course_id,
            "tee_id": tee_row["id"],
        }).eq("id", round_data["id"]).execute()


def delete_round(round_id: str) -> bool:
    """
    Used both to "scrap" a live round (abandon it without finishing) and to
    delete a completed round from the Scoring History page. round_players
    and round_scores rows both cascade-delete via their round_id FK, so
    this is just the one table operation. Doesn't touch any courses/
    course_tees/course_holes rows a finished manual round may have created
    -- those are real course data now, independent of this one round.
    """
    with _timed(f"delete round {round_id}"):
        response = supabase.table("rounds").delete().eq("id", round_id).execute()
    return bool(response.data)


def finish_round(round_id: str) -> dict | None:
    round_data = get_round(round_id)
    if not round_data:
        return None

    if round_data["is_manual"] and not round_data.get("tee_id"):
        owner_entry = next((p for p in round_data["players"] if p["is_owner"]), None)
        if owner_entry:
            _create_course_from_manual_entry(round_data, owner_entry["holes"])
            round_data = get_round(round_id)

    with _timed(f"mark round {round_id} completed"):
        supabase.table("rounds").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", round_id).execute()

    return get_round(round_id)