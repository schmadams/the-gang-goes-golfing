# target path: backend/services/rounds.py (new file)
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from backend.database import supabase
from backend.services.handicaps import get_current_player_handicap


class RoundAlreadyActiveError(Exception):
    """Raised when a player tries to start a round while one is already
    in progress -- the DB's partial unique index is the real guarantee,
    this is just the friendly, catchable version of that rejection."""


class ManualScorecardValidationError(Exception):
    """Raised at finish time if a manual round's hole data is incomplete
    or its stroke indexes aren't a clean 1-18 permutation."""


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


def _hydrate_round(round_row: dict) -> dict:
    """
    Attaches the 18 hole rows (scores + manual data) and display info
    (club/course/tee name) to a bare rounds row. For a non-manual round (or
    a manual one that's already been finished, so tee_id is set), par/
    yardage/stroke_index come from the real course_holes rows so the
    frontend always has one consistent shape to render regardless of
    source.
    """
    round_id = round_row["id"]

    with _timed(f"fetch round_scores for round {round_id}"):
        scores_response = (
            supabase
            .table("round_scores")
            .select("*")
            .eq("round_id", round_id)
            .order("hole_number")
            .execute()
        )
    scores_by_hole = {row["hole_number"]: row for row in (scores_response.data or [])}

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

    holes = []
    for hole_number in range(1, 19):
        score = scores_by_hole.get(hole_number, {})
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

    return {
        **round_row,
        "club_name": club_name,
        "course_name": course_name,
        "tee_name": tee_name,
        "holes": holes,
    }


def get_active_round(player_id: str) -> dict | None:
    with _timed(f"select active round for player {player_id}"):
        response = (
            supabase
            .table("rounds")
            .select("*")
            .eq("player_id", player_id)
            .eq("status", "in_progress")
            .maybe_single()
            .execute()
        )
    round_row = response.data if response is not None else None
    if not round_row:
        return None
    return _hydrate_round(round_row)


def get_round(round_id: str) -> dict | None:
    with _timed(f"select round {round_id}"):
        response = supabase.table("rounds").select("*").eq("id", round_id).maybe_single().execute()
    round_row = response.data if response is not None else None
    if not round_row:
        return None
    return _hydrate_round(round_row)


def list_player_rounds(player_id: str, limit: int = 10) -> list[dict]:
    """
    List for the Rounds History panel (compact) and Scoring History page
    (detailed) -- both read from this one function, since the shape is
    the same either way and the frontend just chooses what to render.
    Includes the player's current in-progress round (if any), first in
    the list, alongside up to `limit` completed rounds -- the `status`
    field (inherited straight from the rounds row) is what lets the
    frontend show a "live" marker instead of needing a separate lookup.
    Each hole carries handicap-adjusted net strokes and Stableford points
    on top of the raw score, putts, and fairway hit.
    """
    with _timed(f"select active round for player {player_id}"):
        active_response = (
            supabase
            .table("rounds")
            .select("*")
            .eq("player_id", player_id)
            .eq("status", "in_progress")
            .maybe_single()
            .execute()
        )
    active_round = active_response.data if active_response is not None else None

    with _timed(f"select completed rounds for player {player_id}"):
        completed_response = (
            supabase
            .table("rounds")
            .select("*")
            .eq("player_id", player_id)
            .eq("status", "completed")
            .order("completed_at", desc=True)
            .limit(limit)
            .execute()
        )
    completed_rounds = completed_response.data or []

    rounds = ([active_round] if active_round else []) + completed_rounds

    with _timed(f"fetch current handicap for player {player_id}"):
        handicap_row = get_current_player_handicap(player_id)
    handicap = handicap_row["handicap"] if handicap_row else None

    summaries = []
    for round_row in rounds:
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

        with _timed(f"fetch round_scores for round {round_row['id']}"):
            scores_response = (
                supabase
                .table("round_scores")
                .select("hole_number, strokes, putts, fairway_hit")
                .eq("round_id", round_row["id"])
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

        summaries.append({
            **round_row,
            "club_name": club_name,
            "course_name": course_name,
            "tee_name": tee_name,
            "total_strokes": sum(strokes_list) if strokes_list else None,
            "holes_played": len(strokes_list),
            "holes": holes,
            "handicap": handicap,
            "total_stableford": sum(stableford_list) if stableford_list else None,
        })

    return summaries


def start_round(payload: dict) -> dict:
    """
    Starts a live round. Pre-checks for an existing active round (fast,
    friendly rejection) but also relies on the DB's partial unique index
    (rounds_one_active_per_player) as the real guarantee under concurrent
    requests -- so a duplicate-key error from the insert itself is also
    caught and turned into the same friendly error.
    """
    player_id = payload["player_id"]

    if get_active_round(player_id):
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

    placeholder_scores = [
        {"round_id": round_row["id"], "hole_number": n} for n in range(1, 19)
    ]
    with _timed(f"insert 18 round_scores placeholders for round {round_row['id']}"):
        supabase.table("round_scores").insert(placeholder_scores).execute()

    return _hydrate_round(round_row)


def update_hole_score(round_id: str, hole_number: int, updates: dict) -> dict | None:
    if not updates:
        return get_round(round_id)

    with _timed(f"update round_scores round={round_id} hole={hole_number}"):
        response = (
            supabase
            .table("round_scores")
            .update(updates)
            .eq("round_id", round_id)
            .eq("hole_number", hole_number)
            .execute()
        )

    if not response.data:
        return None

    return get_round(round_id)


def _create_course_from_manual_entry(round_data: dict) -> None:
    """
    Validates the manually-entered scorecard is complete and internally
    consistent, then writes it as a real courses/course_tees/course_holes
    entry (same tables the UK Golf API import path uses) and links the
    round to it. This is what makes "upload the manual values to the DB"
    happen -- it runs once, at finish time, not per keystroke.
    """
    holes = round_data["holes"]

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
    delete a completed round from the Scoring History page. round_scores
    rows cascade-delete via their FK (round_id ... on delete cascade), so
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
        _create_course_from_manual_entry(round_data)

    with _timed(f"mark round {round_id} completed"):
        supabase.table("rounds").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", round_id).execute()

    return get_round(round_id)