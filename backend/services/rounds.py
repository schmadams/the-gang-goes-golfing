# target path: backend/services/rounds.py (full replacement)
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from backend.database import supabase
from backend.services.club_players import list_players_in_club
from backend.services.friends import list_friends
from backend.services.handicaps import get_current_player_handicap
from backend.services.notifications import create_notification
from backend.services.whs import recalculate_and_store_handicap

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


class TournamentTeeTimeNotFoundError(Exception):
    """Raised when starting a tournament round against a tee_time_id that
    doesn't match any tournament_tee_times row (or whose round/tournament
    has gone missing, checked defensively)."""


class NotInGroupingError(Exception):
    """Raised when a player who isn't assigned to a tee time's grouping
    tries to start that grouping's live round."""


class NotRoundMemberError(Exception):
    """Raised when a player who isn't an accepted participant in a round
    tries to update one of its hole scores, or sign off on / reject it --
    being able to see a round_id/player_id combination (e.g. from the URL)
    isn't enough on its own; the *requester* has to actually belong to the
    round too, not just the player whose hole is being scored."""


class EarlierRoundNotFinishedError(Exception):
    """Raised when a player tries to start a tournament round while an
    earlier round in the same tournament -- one they were actually
    grouped into -- isn't completed yet. Tournament rounds have to be
    played in order; carries the blocking round's round_number so the
    router/frontend can say exactly which round needs finishing first."""

    def __init__(self, round_number: int):
        self.round_number = round_number
        super().__init__(f"Finish Round {round_number} before starting this round.")


class RoundNotPendingSignoffError(Exception):
    """Raised when signing off on, or rejecting, a round that isn't
    currently awaiting sign-off -- e.g. it's still in_progress (nobody's
    submitted a scorecard to approve yet), a solo round that finished
    straight to completed, or already fully signed off by everyone."""


class RoundNotEditableError(Exception):
    """Raised when trying to update a hole score on a round that isn't
    in_progress. Once a multiplayer round moves to pending_signoff, its
    scorecard is meant to be a frozen, submitted-for-review snapshot --
    not something any player can keep quietly editing while others are
    deciding whether to approve it. A rejected round resets back to
    in_progress (see reject_round_signoff) and becomes editable again from
    there."""


class NotRoundCreatorError(Exception):
    """Raised by delete_round when a non-creator tries to Scrap a casual
    round that's still in_progress -- that deletes the round for every
    player still in it, not just themselves, so only the round's actual
    creator (rounds.player_id) can do it. Doesn't apply to a tournament
    round's Scrap (every grouping member stays equally able to do that,
    same as always -- there's no single privileged creator among an equal
    grouping, see start_tournament_round) or to deleting an already-
    finished round from Scoring History (a different action -- "clean up
    my own history" -- left open to any participant)."""


class CannotLeaveRoundError(Exception):
    """Raised by leave_round in either of two cases: the round's own
    creator tried to leave it (Scrap Round is their equivalent action --
    it removes the round for everyone, since there'd be nobody left to
    hand it to otherwise), or the round is a tournament round at all
    (those represent an official competition round for a whole tee-time
    grouping, not a casual game a player can just step out of -- a
    player who can't continue a tournament round is a DNF/No Result
    concept instead, not something leave_round covers)."""


class RoundNotInProgressError(Exception):
    """Raised by leave_round, and by mark_round_no_result, when the round
    isn't in_progress anymore. Once a round reaches pending_signoff, every
    remaining player's approval already depends on exactly who was in the
    round and what their scorecard said at that point -- leaving, or
    marking No Result, after the fact would need to reopen sign-off for
    everyone else too, a bigger behavior change than either plain
    self-service action implies -- so both are only ever allowed while
    still in_progress."""


class CannotMarkNoResultError(Exception):
    """Raised by mark_round_no_result when the round isn't a tournament
    round. No Return is a competition-scoring concept -- there's no
    leaderboard for a casual round played with friends to sort someone to
    the bottom of, and a casual round's equivalent "I can't continue" is
    already covered by leave_round (which removes the player from the
    round entirely, rather than filling their card with NR)."""


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
    real infrastructure. Only ever targets in_progress -- a round sitting
    in pending_signoff waiting on people is never auto-scrapped just for
    taking a while to collect everyone's sign-off."""
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


def _get_active_round_id_for_player(player_id: str, tournament_scope: bool = False) -> str | None:
    """The one round (if any) this player is currently an accepted
    participant in -- owner or joined, doesn't matter which. This is what
    the "one active round per player" rule now actually checks, since a
    player can be tied up in someone else's round without owning it.

    tournament_scope splits this into two independent pools rather than
    one -- False (the default, used everywhere casual rounds care about
    "do I already have an active round") only looks at rounds with
    tournament_round_id IS NULL; True (used by start_tournament_round)
    only looks at rounds with tournament_round_id IS NOT NULL. That's what
    lets a player have one active casual round *and* one active tournament
    round running at the same time without either blocking the other --
    see add_tournament_live_rounds.sql's comment on the rescoped
    rounds_one_active_per_player index, which enforces the same split at
    the database level for the casual side.

    Deliberately only ever matches status='in_progress' -- a round sitting
    in pending_signoff isn't "active" in the sense this guards (you can't
    still be adding scores to it), so it doesn't block starting a new one.
    The thing that *does* block starting a new tournament round while an
    earlier one awaits sign-off is _first_unfinished_prior_round_number's
    status != 'completed' check, not this."""
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

    with _timed(f"select in_progress round among player {player_id}'s rounds (tournament_scope={tournament_scope})"):
        query = (
            supabase
            .table("rounds")
            .select("id")
            .in_("id", round_ids)
            .eq("status", "in_progress")
        )
        query = query.not_.is_("tournament_round_id", "null") if tournament_scope else query.is_("tournament_round_id", "null")
        rounds_response = query.limit(1).execute()
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
            # None for a solo round (never needed sign-off) or a
            # multiplayer round this player hasn't signed off on yet --
            # see add_round_signoff.sql / sign_off_round.
            "signed_off_at": rp.get("signed_off_at"),
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
                "nr": score.get("nr", False),
                "manual_par": score.get("manual_par"),
                "manual_yardage": score.get("manual_yardage"),
                "manual_stroke_index": score.get("manual_stroke_index"),
                "par": course_hole.get("par"),
                "yardage": course_hole.get("yardage"),
                "stroke_index": course_hole.get("stroke_index"),
            })
        entry["holes"] = holes
        players.append(entry)

    tournament_context = {}
    if round_row.get("tournament_round_id"):
        tournament_context = _tournament_context_for_round(round_row["tournament_round_id"])

    return {
        **round_row,
        "club_name": club_name,
        "course_name": course_name,
        "tee_name": tee_name,
        "players": players,
        "pending_invites": pending_invites,
        **tournament_context,
    }


def _tournament_context_for_round(tournament_round_id: str) -> dict:
    """tournament_id/tournament_name/tournament_round_number/club_slug for
    a tournament-linked round -- lets the live round page show its way
    back into the tournament (a subnav matching the tournament page's own,
    plus Return to Club) instead of a dead end, and lets round_header_
    label (components/scorecard.py) show "club — tournament — Round N"
    instead of the plain club/course/tee format, so a tournament round
    never looks like just another casual round wherever it's displayed --
    the home page's Live Round panel, Rounds History, Scoring History.
    One query via a nested embed (tournament_rounds -> tournaments ->
    clubs) rather than four sequential fetches."""
    with _timed(f"fetch tournament context for round {tournament_round_id}"):
        response = (
            supabase
            .table("tournament_rounds")
            .select("tournament_id, round_number, tournaments(name, clubs(slug))")
            .eq("id", tournament_round_id)
            .maybe_single()
            .execute()
        )
    row = response.data if response is not None else None
    if not row:
        return {}

    tournament = row.get("tournaments") or {}
    club = tournament.get("clubs") or {}
    return {
        "tournament_id": row.get("tournament_id"),
        "tournament_name": tournament.get("name"),
        "tournament_round_number": row.get("round_number"),
        "club_slug": club.get("slug"),
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
        _apply_viewer_is_owner(hydrated, round_row, viewer_player_id)
    return hydrated


def _apply_viewer_is_owner(hydrated: dict, round_row: dict, viewer_player_id: str) -> None:
    """Sets hydrated["is_owner"] relative to whichever player is looking
    at this round -- shared by get_round and list_pending_signoff_rounds
    so both compute it the same way instead of drifting."""
    if round_row.get("tournament_round_id"):
        # Tournament rounds don't have a single privileged owner the way
        # casual rounds do -- every grouping member was made an equal
        # accepted participant when the round was started (see
        # start_tournament_round), so "can this viewer Finish/Scrap/sign
        # off on it" means "are they one of the accepted players", not
        # "are they specifically whoever happened to tap Start first".
        hydrated["is_owner"] = any(p["player_id"] == viewer_player_id for p in hydrated["players"])
    else:
        hydrated["is_owner"] = round_row["player_id"] == viewer_player_id


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


def _batch_fetch_round_hydration(rounds: list[dict], player_id: str) -> tuple[dict, dict, dict]:
    """
    Prefetches everything _build_round_summary needs for a whole batch of
    rounds in 3 queries total, instead of the 2-3 queries *per round* it
    used to run (list_player_rounds' home page load went from ~15
    sequential round trips for 5 rounds to 3 -- worse the more rounds
    share the same tee, since each repeat used to refetch identical tee/
    course_holes data). Returns (tee_by_id, holes_by_tee_id,
    scores_by_round_id) -- each a plain in-memory lookup, safe to reuse
    across every round in the batch.
    """
    tee_ids = list({r["tee_id"] for r in rounds if r.get("tee_id")})
    round_ids = [r["id"] for r in rounds]

    tee_by_id: dict[str, dict] = {}
    holes_by_tee_id: dict[str, dict[int, dict]] = {}
    if tee_ids:
        with _timed(f"fetch {len(tee_ids)} tee(s) (+courses) for round batch"):
            tees_response = (
                supabase
                .table("course_tees")
                .select("*, courses(club_name, course_name)")
                .in_("id", tee_ids)
                .execute()
            )
        tee_by_id = {t["id"]: t for t in (tees_response.data or [])}

        with _timed(f"fetch course_holes for {len(tee_ids)} tee(s) in round batch"):
            holes_response = (
                supabase
                .table("course_holes")
                .select("*")
                .in_("tee_id", tee_ids)
                .order("hole_number")
                .execute()
            )
        for hole in (holes_response.data or []):
            holes_by_tee_id.setdefault(hole["tee_id"], {})[hole["hole_number"]] = hole

    scores_by_round_id: dict[str, dict[int, dict]] = {}
    if round_ids:
        with _timed(f"fetch round_scores for {len(round_ids)} round(s) player {player_id}"):
            scores_response = (
                supabase
                .table("round_scores")
                .select("round_id, hole_number, strokes, putts, fairway_hit, nr")
                .in_("round_id", round_ids)
                .eq("player_id", player_id)
                .order("hole_number")
                .execute()
            )
        for score in (scores_response.data or []):
            scores_by_round_id.setdefault(score["round_id"], {})[score["hole_number"]] = score

    return tee_by_id, holes_by_tee_id, scores_by_round_id


def _build_round_summary(
    round_row: dict,
    player_id: str,
    handicap: float | None,
    tee_by_id: dict,
    holes_by_tee_id: dict,
    scores_by_round_id: dict,
) -> dict:
    """
    Shared by list_player_rounds (Rounds History / Scoring History) and
    get_player_analysis (Player Analysis charts) -- both need the same
    per-hole hydration (course/tee names, par/stroke index from
    course_holes, and handicap-adjusted net strokes / Stableford points
    per hole) for one specific player's own scorecard within a round they
    belong to (owner or accepted participant), not everyone's.

    Takes the batch-prefetched lookups from _batch_fetch_round_hydration
    instead of querying itself -- callers run that once per batch, not
    once per round.
    """
    club_name = round_row.get("manual_club_name")
    course_name = None
    tee_name = round_row.get("manual_tee_name")
    course_holes_by_number = {}

    tee = tee_by_id.get(round_row.get("tee_id"))
    if tee:
        tee_name = tee["name"]
        course_info = tee.get("courses") or {}
        club_name = course_info.get("club_name") or club_name
        course_name = course_info.get("course_name")
        course_holes_by_number = holes_by_tee_id.get(tee["id"], {})

    scores_by_hole = scores_by_round_id.get(round_row["id"], {})

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
            "yardage": course_hole.get("yardage"),
            "strokes": strokes,
            "putts": score.get("putts"),
            "fairway_hit": score.get("fairway_hit"),
            "nr": score.get("nr", False),
            "net_strokes": net_strokes,
            "stableford_points": stableford_points,
        })

    strokes_list = [h["strokes"] for h in holes if h["strokes"] is not None]
    stableford_list = [h["stableford_points"] for h in holes if h["stableford_points"] is not None]

    # Same tournament context _hydrate_round attaches for the live-round
    # page -- needed here too so the home page's Live Round panel and
    # Rounds History (both built from list_player_rounds) can tell a
    # tournament round apart from a casual one, not just the live-round
    # detail view. Not batched across the round list the way tee/course_
    # holes/scores are above -- most players have few or no tournament
    # rounds in their history, so one extra query apiece is cheap in
    # practice, and keeping this self-contained here means callers don't
    # need their own batching logic just for this.
    tournament_context = {}
    if round_row.get("tournament_round_id"):
        tournament_context = _tournament_context_for_round(round_row["tournament_round_id"])

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
        **tournament_context,
    }


def list_player_rounds(player_id: str, limit: int = 20) -> list[dict]:
    """
    List for the Rounds History panel (compact) and Scoring History page
    (detailed) -- both read from this one function. Covers every round
    this player belongs to, as owner or accepted participant, not just
    ones they started. Includes any in-progress round and any
    pending_signoff round (both surfaced ahead of the completed bucket,
    unlimited) alongside up to `limit` completed rounds -- the `status`
    field is what lets the frontend show a "live" marker or a "pending
    sign-off" badge instead of needing a separate lookup.
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

    with _timed(f"select pending_signoff rounds among player {player_id}'s rounds"):
        pending_signoff_response = (
            supabase
            .table("rounds")
            .select("*")
            .in_("id", round_ids)
            .eq("status", "pending_signoff")
            .order("completed_at", desc=True)
            .execute()
        )
    pending_signoff_rounds = pending_signoff_response.data or []

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

    rounds = active_rounds + pending_signoff_rounds + completed_rounds

    with _timed(f"fetch current handicap for player {player_id}"):
        handicap_row = get_current_player_handicap(player_id)
    handicap = handicap_row["handicap"] if handicap_row else None

    tee_by_id, holes_by_tee_id, scores_by_round_id = _batch_fetch_round_hydration(rounds, player_id)
    return [
        _build_round_summary(round_row, player_id, handicap, tee_by_id, holes_by_tee_id, scores_by_round_id)
        for round_row in rounds
    ]


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

    tee_by_id, holes_by_tee_id, scores_by_round_id = _batch_fetch_round_hydration(completed_rounds, player_id)

    points = []
    for round_row in completed_rounds:
        summary = _build_round_summary(
            round_row, player_id, None, tee_by_id, holes_by_tee_id, scores_by_round_id
        )
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


# Bucket labels for get_player_scoring_profile's scoring_breakdown --
# order matters (best to worst), the frontend renders bars in this same
# order. "double_bogey_plus" folds triple-bogey-and-worse in with double
# bogey rather than giving every possible score its own bucket, matching
# how the request was phrased ("birdies, pars, bogeys, double bogeys and
# worse").
def _scoring_bucket(diff: int) -> str:
    if diff <= -1:
        return "birdie_or_better"
    if diff == 0:
        return "par"
    if diff == 1:
        return "bogey"
    return "double_bogey_plus"


def get_player_scoring_profile(player_id: str) -> dict:
    """
    Two aggregate views across every completed round this player belongs
    to (owner or accepted participant), for the Player Analysis page's
    "Score to Par by Hole Type" and "Scoring Breakdown" charts:

    - par_type_breakdown: average (strokes - par) on par-3s, par-4s, and
      par-5s separately -- lets a player see which hole length is
      actually costing them strokes, rather than one blended average
      across every hole.
    - scoring_breakdown: how many birdies-or-better / pars / bogeys /
      double-bogeys-or-worse they card, on average, per round (see
      _scoring_bucket) -- "per round" reads as "per 18 holes" for a
      normal full round, which is how every completed round in this app
      is played.

    Both skip holes with no recorded strokes or no known par (NR'd holes,
    or manual rounds missing course data) -- only holes with a real,
    comparable score-to-par count toward either breakdown.
    """
    with _timed(f"select accepted round_players for player {player_id} (scoring profile)"):
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
        return {"par_type_breakdown": [], "scoring_breakdown": [], "rounds_counted": 0}

    with _timed(f"select completed rounds among player {player_id}'s rounds (scoring profile)"):
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
    if not completed_rounds:
        return {"par_type_breakdown": [], "scoring_breakdown": [], "rounds_counted": 0}

    tee_by_id, holes_by_tee_id, scores_by_round_id = _batch_fetch_round_hydration(completed_rounds, player_id)

    diffs_by_par = {3: [], 4: [], 5: []}
    bucket_counts = {"birdie_or_better": 0, "par": 0, "bogey": 0, "double_bogey_plus": 0}
    rounds_counted = 0

    for round_row in completed_rounds:
        summary = _build_round_summary(
            round_row, player_id, None, tee_by_id, holes_by_tee_id, scores_by_round_id
        )
        round_had_a_valid_hole = False
        for hole in summary["holes"]:
            if hole.get("nr") or hole.get("strokes") is None or hole.get("par") not in (3, 4, 5):
                continue
            round_had_a_valid_hole = True
            diff = hole["strokes"] - hole["par"]
            diffs_by_par[hole["par"]].append(diff)
            bucket_counts[_scoring_bucket(diff)] += 1
        if round_had_a_valid_hole:
            rounds_counted += 1

    par_type_breakdown = [
        {
            "par": par,
            "holes_played": len(diffs),
            "avg_score_to_par": round(sum(diffs) / len(diffs), 2) if diffs else None,
        }
        for par, diffs in diffs_by_par.items()
    ]

    scoring_breakdown = [
        {
            "category": category,
            "total": count,
            "avg_per_round": round(count / rounds_counted, 2) if rounds_counted else None,
        }
        for category, count in bucket_counts.items()
    ]

    return {
        "par_type_breakdown": par_type_breakdown,
        "scoring_breakdown": scoring_breakdown,
        "rounds_counted": rounds_counted,
    }


# Bin boundaries for get_player_distance_profile -- covers a typical
# short par 3 up through a long par 5, in the same rough bands a real
# scorecard's "yardage" column falls into. (label, lo, hi) with either
# bound as None meaning unbounded on that side (open-ended top bucket for
# 450y+, no lower bound needed for the first bucket since yardage can't
# be negative).
_DISTANCE_BINS = [
    ("< 150y", None, 149),
    ("150-249y", 150, 249),
    ("250-349y", 250, 349),
    ("350-449y", 350, 449),
    ("450y+", 450, None),
]


def _distance_bin_label(yardage: int) -> str | None:
    for label, lo, hi in _DISTANCE_BINS:
        if (lo is None or yardage >= lo) and (hi is None or yardage <= hi):
            return label
    return None


def get_player_distance_profile(player_id: str) -> dict:
    """
    Average shots (raw strokes taken, not score-to-par -- see the
    existing par_type_breakdown above for the to-par view) per hole-
    distance bin, across every completed round this player belongs to.
    Skips any hole missing strokes, an NR mark, or a known yardage (a
    manual round with no course data attached has no yardage to bin by,
    so those holes just don't contribute here -- they still count
    everywhere else that doesn't need distance).
    """
    with _timed(f"select accepted round_players for player {player_id} (distance profile)"):
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
        return {"distance_breakdown": []}

    with _timed(f"select completed rounds among player {player_id}'s rounds (distance profile)"):
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
    if not completed_rounds:
        return {"distance_breakdown": []}

    tee_by_id, holes_by_tee_id, scores_by_round_id = _batch_fetch_round_hydration(completed_rounds, player_id)

    strokes_by_bin = {label: [] for label, _, _ in _DISTANCE_BINS}

    for round_row in completed_rounds:
        summary = _build_round_summary(
            round_row, player_id, None, tee_by_id, holes_by_tee_id, scores_by_round_id
        )
        for hole in summary["holes"]:
            if hole.get("nr") or hole.get("strokes") is None or hole.get("yardage") is None:
                continue
            label = _distance_bin_label(hole["yardage"])
            if label is not None:
                strokes_by_bin[label].append(hole["strokes"])

    distance_breakdown = [
        {
            "bin": label,
            "holes_played": len(strokes),
            "avg_strokes": round(sum(strokes) / len(strokes), 2) if strokes else None,
        }
        for label, strokes in strokes_by_bin.items()
    ]

    return {"distance_breakdown": distance_breakdown}


def get_player_scoring_history(player_id: str) -> list[dict]:
    """
    Chronological (oldest -> newest) round-level scoring points for the
    Scoring History chart's Validated / Tournament / All tabs -- one
    entry per round with just enough metadata (validated, is_tournament)
    for the frontend to filter into whichever tab is active client-side,
    rather than three separate backend calls for what's really one
    underlying list.

    Includes completed AND pending_signoff rounds -- a pending_signoff
    round already has a full scorecard (every player just hasn't signed
    off on it yet), so it has a real score to show on the "All" tab, it
    just isn't "validated" (counted toward Handicap Index) yet. "Validated"
    filters this same list down to status == "completed" only; "Tournament"
    filters down to rounds with a tournament_round_id.
    """
    with _timed(f"select accepted round_players for player {player_id} (scoring history)"):
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

    with _timed(f"select completed/pending_signoff rounds among player {player_id}'s rounds (scoring history)"):
        response = (
            supabase
            .table("rounds")
            .select("*")
            .in_("id", round_ids)
            .in_("status", ["completed", "pending_signoff"])
            .order("completed_at", desc=False)
            .limit(200)
            .execute()
        )
    rounds = response.data or []
    if not rounds:
        return []

    tee_by_id, holes_by_tee_id, scores_by_round_id = _batch_fetch_round_hydration(rounds, player_id)

    points = []
    for round_row in rounds:
        summary = _build_round_summary(
            round_row, player_id, None, tee_by_id, holes_by_tee_id, scores_by_round_id
        )
        if summary["total_strokes"] is None:
            continue  # no recorded strokes at all yet -- nothing to plot
        points.append({
            "date": (round_row.get("completed_at") or "")[:10],
            "total_strokes": summary["total_strokes"],
            "validated": round_row.get("status") == "completed",
            "is_tournament": bool(round_row.get("tournament_round_id")),
        })

    return points


def _batch_fetch_multi_player_hydration(rounds: list[dict]) -> tuple[dict, dict, dict]:
    """
    Same tee/course_holes lookups as _batch_fetch_round_hydration, but the
    scores lookup isn't scoped to a single player -- get_club_player_
    comparison needs every accepted player's scorecard for each round in
    scope, not just one player's. scores_by_round_and_player is keyed
    round_id -> player_id -> hole_number, one extra level of nesting
    versus _batch_fetch_round_hydration's round_id -> hole_number.
    """
    tee_ids = list({r["tee_id"] for r in rounds if r.get("tee_id")})
    round_ids = [r["id"] for r in rounds]

    tee_by_id: dict[str, dict] = {}
    holes_by_tee_id: dict[str, dict[int, dict]] = {}
    if tee_ids:
        with _timed(f"fetch {len(tee_ids)} tee(s) (+courses) for club comparison batch"):
            tees_response = (
                supabase
                .table("course_tees")
                .select("*, courses(club_name, course_name)")
                .in_("id", tee_ids)
                .execute()
            )
        tee_by_id = {t["id"]: t for t in (tees_response.data or [])}

        with _timed(f"fetch course_holes for {len(tee_ids)} tee(s) in club comparison batch"):
            holes_response = (
                supabase
                .table("course_holes")
                .select("*")
                .in_("tee_id", tee_ids)
                .order("hole_number")
                .execute()
            )
        for hole in (holes_response.data or []):
            holes_by_tee_id.setdefault(hole["tee_id"], {})[hole["hole_number"]] = hole

    scores_by_round_and_player: dict[str, dict[str, dict[int, dict]]] = {}
    if round_ids:
        with _timed(f"fetch round_scores for {len(round_ids)} round(s) (club comparison, all players)"):
            scores_response = (
                supabase
                .table("round_scores")
                .select("round_id, player_id, hole_number, strokes, putts, fairway_hit, nr")
                .in_("round_id", round_ids)
                .order("hole_number")
                .execute()
            )
        for score in (scores_response.data or []):
            scores_by_round_and_player.setdefault(score["round_id"], {}).setdefault(
                score["player_id"], {}
            )[score["hole_number"]] = score

    return tee_by_id, holes_by_tee_id, scores_by_round_and_player


def _build_holes_for_scores(
    round_row: dict, scores_by_hole: dict, tee_by_id: dict, holes_by_tee_id: dict
) -> list[dict]:
    """
    Same per-hole shape _build_round_summary builds (par, yardage,
    strokes, putts, fairway_hit, nr) minus the handicap-adjusted
    net_strokes/stableford_points fields -- get_club_player_comparison
    only needs raw scores (same as every other analysis chart, which are
    all to-par or raw-strokes based already) and is comparing many
    players who don't all share one handicap, so there's no single
    handicap to adjust by here anyway.
    """
    course_holes_by_number = {}
    tee = tee_by_id.get(round_row.get("tee_id"))
    if tee:
        course_holes_by_number = holes_by_tee_id.get(tee["id"], {})

    holes = []
    for hole_number in range(1, 19):
        course_hole = course_holes_by_number.get(hole_number, {})
        score = scores_by_hole.get(hole_number, {})
        holes.append({
            "hole_number": hole_number,
            "par": course_hole.get("par"),
            "yardage": course_hole.get("yardage"),
            "strokes": score.get("strokes"),
            "putts": score.get("putts"),
            "fairway_hit": score.get("fairway_hit"),
            "nr": score.get("nr", False),
        })
    return holes


def _empty_club_comparison() -> dict:
    return {
        "players": [],
        "putts": {},
        "fairway": {},
        "scoring_history": {},
        "par_type": {},
        "scoring_breakdown": {},
        "distance_profile": {},
    }


def get_club_player_comparison(club_id: str, window: int = 5) -> dict:
    """
    Player-vs-player version of the Player Analysis charts, scoped to
    just this club's rounds -- a round only counts here if it's tied to
    this club specifically, two ways:
      - a tournament round belonging to a tournament this club hosted
        (tournaments.club_id -> tournament_rounds.tournament_id ->
        rounds.tournament_round_id), or
      - a casual round where at least *two* of the accepted players are
        members of this club. No manual tagging involved (there used to
        be a rounds.club_id a player could optionally set when starting a
        round -- that's gone; see backend/models/round.py's own note on
        RoundStartRequest). The two-member floor is what keeps this
        meaningfully "the club played together" rather than just "a
        member happened to play" -- one member's round with friends who
        aren't in the club is that player's own business, not something
        the whole club should see plotted here (they still get it on
        their own Player Analysis page either way). A round counts for
        every club it clears that floor for, automatically and
        simultaneously -- if all of a round's players belong to the same
        club it counts fully for that club, and if only some of them do
        (say 2 of 3), it still counts, just with only the member(s)' own
        scorecards plotted -- the non-member player(s) simply don't get a
        trace here (see the round_players membership filter just below,
        which is what actually enforces that; it applies identically
        regardless of whether every player happened to be a member or
        just some were).
    A club member's rounds anywhere else -- a solo/1-member-only casual
    round, or a tournament round for a different club -- never counts
    here, even though it'd show up on that player's own Player Analysis
    page.

    Only club members are compared (a non-member who played alongside one
    still gets their own scorecard on the round itself, just not a spot
    in this comparison), and only members with at least one qualifying
    round show up at all -- a member who hasn't played a round with any
    fellow member (or in one of this club's tournaments) yet has nothing
    to plot.

    Returns per-player series in the same shapes the single-player
    functions already use (get_player_analysis / get_player_scoring_
    profile / get_player_distance_profile / get_player_scoring_history),
    just keyed by player_id instead of being for one player, so the
    frontend can draw one trace per player on shared axes:
      {
        "players": [{"player_id", "name"}, ...],
        "putts": {player_id: [{"date", "putts_total", "putts_rolling_avg"}, ...]},
        "fairway": {player_id: [{"date", "fairway_pct", "fairway_rolling_avg"}, ...]},
        "scoring_history": {player_id: [{"date", "total_strokes"}, ...]},
        "par_type": {player_id: [{"par", "avg_score_to_par"}, ...]},
        "scoring_breakdown": {player_id: [{"category", "avg_per_round"}, ...]},
        "distance_profile": {player_id: [{"bin", "avg_strokes"}, ...]},
      }
    """
    with _timed(f"select roster for club {club_id} (comparison)"):
        roster = list_players_in_club(club_id)
    if not roster:
        return _empty_club_comparison()

    member_ids = {row["player_id"] for row in roster}
    name_by_player = {}
    photo_by_player = {}
    for row in roster:
        info = row.get("players") or {}
        name_by_player[row["player_id"]] = (
            info.get("nickname")
            or f"{info.get('first_name', '')} {info.get('surname', '')}".strip()
            or "Unknown"
        )
        photo_by_player[row["player_id"]] = info.get("profile_picture_url")

    with _timed(f"select tournaments for club {club_id} (comparison)"):
        tournaments_response = supabase.table("tournaments").select("id").eq("club_id", club_id).execute()
    tournament_ids = [t["id"] for t in (tournaments_response.data or [])]

    tournament_round_ids = []
    if tournament_ids:
        with _timed(f"select tournament_rounds for club {club_id} (comparison)"):
            tr_response = (
                supabase
                .table("tournament_rounds")
                .select("id")
                .in_("tournament_id", tournament_ids)
                .execute()
            )
        tournament_round_ids = [r["id"] for r in (tr_response.data or [])]

    # Keyed by round id to naturally dedupe -- a round can only ever match
    # one of the two paths below in practice (a tournament round's
    # tournament_round_id path, or a casual round with a member in it),
    # but a dict here costs nothing and removes any doubt.
    scoped_rounds: dict[str, dict] = {}

    if tournament_round_ids:
        with _timed(f"select tournament rounds for club {club_id}'s tournaments (comparison)"):
            tourney_rounds_response = (
                supabase
                .table("rounds")
                .select("*")
                .in_("tournament_round_id", tournament_round_ids)
                .eq("status", "completed")
                .execute()
            )
        for row in (tourney_rounds_response.data or []):
            scoped_rounds[row["id"]] = row

    # Casual rounds -- no rounds.club_id tag to filter on any more (see
    # this function's own docstring), so instead: find every round any
    # member of this club was an accepted player in, then fetch those
    # rounds directly. tournament_round_id IS NULL keeps this to casual
    # rounds only -- a tournament round with a member in it is already
    # covered (or not) by the tournament_round_ids path above, and
    # letting it through here too would risk pulling in a round for a
    # *different* club's tournament just because one of this club's
    # members happened to be entered in it.
    with _timed(f"select round_players for club {club_id} members (comparison)"):
        member_round_players_response = (
            supabase
            .table("round_players")
            .select("round_id")
            .in_("player_id", list(member_ids))
            .eq("status", "accepted")
            .execute()
        )
    member_counts_by_round: dict[str, int] = {}
    for rp in (member_round_players_response.data or []):
        member_counts_by_round[rp["round_id"]] = member_counts_by_round.get(rp["round_id"], 0) + 1
    # >= 2, not >= 1 -- see this function's own docstring for why a
    # single member playing with non-member friends shouldn't count.
    member_round_ids = [round_id for round_id, count in member_counts_by_round.items() if count >= 2]

    if member_round_ids:
        with _timed(f"select casual rounds for club {club_id} members (comparison)"):
            casual_rounds_response = (
                supabase
                .table("rounds")
                .select("*")
                .in_("id", member_round_ids)
                .eq("status", "completed")
                .is_("tournament_round_id", "null")
                .execute()
            )
        for row in (casual_rounds_response.data or []):
            scoped_rounds[row["id"]] = row

    rounds = sorted(scoped_rounds.values(), key=lambda r: r.get("completed_at") or "")
    if not rounds:
        return _empty_club_comparison()

    round_ids = [r["id"] for r in rounds]

    with _timed(f"select accepted round_players for club {club_id}'s scoped rounds (comparison)"):
        rp_response = (
            supabase
            .table("round_players")
            .select("round_id, player_id")
            .in_("round_id", round_ids)
            .eq("status", "accepted")
            .execute()
        )
    players_by_round: dict[str, list[str]] = {}
    for rp in (rp_response.data or []):
        if rp["player_id"] in member_ids:
            players_by_round.setdefault(rp["round_id"], []).append(rp["player_id"])

    tee_by_id, holes_by_tee_id, scores_by_round_and_player = _batch_fetch_multi_player_hydration(rounds)

    putts_points: dict[str, list[dict]] = {}
    fairway_points: dict[str, list[dict]] = {}
    scoring_history_points: dict[str, list[dict]] = {}
    diffs_by_par_by_player: dict[str, dict[int, list[int]]] = {}
    bucket_counts_by_player: dict[str, dict[str, int]] = {}
    rounds_counted_by_player: dict[str, int] = {}
    distance_totals_by_player: dict[str, dict[str, list[int]]] = {}

    for round_row in rounds:
        date = (round_row.get("completed_at") or "")[:10]
        for player_id in players_by_round.get(round_row["id"], []):
            scores_by_hole = scores_by_round_and_player.get(round_row["id"], {}).get(player_id, {})
            holes = _build_holes_for_scores(round_row, scores_by_hole, tee_by_id, holes_by_tee_id)

            putts_total, fairway_pct = _round_putts_and_fairway_pct(holes)
            putts_points.setdefault(player_id, []).append(
                {"date": date, "putts_total": putts_total, "putts_rolling_avg": None}
            )
            fairway_points.setdefault(player_id, []).append(
                {"date": date, "fairway_pct": fairway_pct, "fairway_rolling_avg": None}
            )

            strokes_list = [h["strokes"] for h in holes if h["strokes"] is not None]
            if strokes_list:
                scoring_history_points.setdefault(player_id, []).append(
                    {"date": date, "total_strokes": sum(strokes_list)}
                )

            diffs_by_par = diffs_by_par_by_player.setdefault(player_id, {3: [], 4: [], 5: []})
            bucket_counts = bucket_counts_by_player.setdefault(
                player_id, {"birdie_or_better": 0, "par": 0, "bogey": 0, "double_bogey_plus": 0}
            )
            distance_totals = distance_totals_by_player.setdefault(player_id, {})
            round_had_a_valid_hole = False
            for hole in holes:
                if hole.get("nr") or hole.get("strokes") is None:
                    continue
                if hole.get("par") in (3, 4, 5):
                    round_had_a_valid_hole = True
                    diff = hole["strokes"] - hole["par"]
                    diffs_by_par[hole["par"]].append(diff)
                    bucket_counts[_scoring_bucket(diff)] += 1
                if hole.get("yardage") is not None:
                    bin_label = _distance_bin_label(hole["yardage"])
                    if bin_label:
                        distance_totals.setdefault(bin_label, []).append(hole["strokes"])
            if round_had_a_valid_hole:
                rounds_counted_by_player[player_id] = rounds_counted_by_player.get(player_id, 0) + 1

    # Rolling averages, per player, over each stat's own series
    # independently -- identical windowing to get_player_analysis, just
    # repeated once per player instead of once total.
    for points in putts_points.values():
        series = [p for p in points if p["putts_total"] is not None]
        for i, p in enumerate(series):
            window_vals = [x["putts_total"] for x in series[max(0, i - window + 1):i + 1]]
            p["putts_rolling_avg"] = round(sum(window_vals) / len(window_vals), 1)

    for points in fairway_points.values():
        series = [p for p in points if p["fairway_pct"] is not None]
        for i, p in enumerate(series):
            window_vals = [x["fairway_pct"] for x in series[max(0, i - window + 1):i + 1]]
            p["fairway_rolling_avg"] = round(sum(window_vals) / len(window_vals), 1)

    qualifying_player_ids = (
        {pid for pid, points in scoring_history_points.items() if points}
        | {pid for pid, points in putts_points.items() if any(p["putts_total"] is not None for p in points)}
        | {pid for pid, points in fairway_points.items() if any(p["fairway_pct"] is not None for p in points)}
    )

    players = [
        {
            "player_id": pid,
            "name": name_by_player.get(pid, "Unknown"),
            # Same "carry the real photo through, fall back to initials
            # client-side" pattern as get_tournament_leaderboard -- roster
            # rows already embed the full players(*) row (see
            # list_players_in_club), so profile_picture_url was already
            # sitting right there.
            "photo_url": photo_by_player.get(pid),
        }
        for pid in sorted(qualifying_player_ids, key=lambda pid: name_by_player.get(pid, ""))
    ]

    par_type: dict[str, list[dict]] = {}
    scoring_breakdown: dict[str, list[dict]] = {}
    distance_profile: dict[str, list[dict]] = {}
    for pid in qualifying_player_ids:
        diffs_by_par = diffs_by_par_by_player.get(pid, {3: [], 4: [], 5: []})
        par_type[pid] = [
            {"par": par, "avg_score_to_par": round(sum(diffs) / len(diffs), 2) if diffs else None}
            for par, diffs in diffs_by_par.items()
        ]

        bucket_counts = bucket_counts_by_player.get(pid, {})
        rounds_counted = rounds_counted_by_player.get(pid, 0)
        scoring_breakdown[pid] = [
            {
                "category": category,
                "avg_per_round": round(count / rounds_counted, 2) if rounds_counted else None,
            }
            for category, count in bucket_counts.items()
        ]

        distance_totals = distance_totals_by_player.get(pid, {})
        distance_profile[pid] = [
            {"bin": label, "avg_strokes": round(sum(vals) / len(vals), 2)}
            for label, _lo, _hi in _DISTANCE_BINS
            if (vals := distance_totals.get(label))
        ]

    return {
        "players": players,
        "putts": {pid: putts_points.get(pid, []) for pid in qualifying_player_ids},
        "fairway": {pid: fairway_points.get(pid, []) for pid in qualifying_player_ids},
        "scoring_history": {pid: scoring_history_points.get(pid, []) for pid in qualifying_player_ids},
        "par_type": par_type,
        "scoring_breakdown": scoring_breakdown,
        "distance_profile": distance_profile,
    }


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
        "manual_course_rating": payload.get("manual_course_rating"),
        "manual_slope_rating": payload.get("manual_slope_rating"),
        # No club_id written here any more -- get_club_player_comparison
        # now figures out which clubs a casual round counts toward itself
        # (by round_players membership), rather than relying on a tag set
        # at creation time. See that function's own docstring.
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


def _get_tee_time(tee_time_id: str) -> dict | None:
    with _timed(f"select tournament_tee_times {tee_time_id}"):
        response = (
            supabase.table("tournament_tee_times").select("*").eq("id", tee_time_id).maybe_single().execute()
        )
    return response.data if response is not None else None


def _tee_time_player_ids(tee_time_id: str) -> list[str]:
    with _timed(f"select tournament_tee_time_players for slot {tee_time_id}"):
        response = (
            supabase
            .table("tournament_tee_time_players")
            .select("player_id")
            .eq("tee_time_id", tee_time_id)
            .execute()
        )
    return [row["player_id"] for row in (response.data or [])]


def _get_tournament_round(tournament_round_id: str) -> dict | None:
    with _timed(f"select tournament_rounds {tournament_round_id}"):
        response = (
            supabase.table("tournament_rounds").select("*").eq("id", tournament_round_id).maybe_single().execute()
        )
    return response.data if response is not None else None


def _find_in_progress_round_for_tee_time(tee_time_id: str) -> dict | None:
    with _timed(f"select in_progress round for tee_time {tee_time_id}"):
        response = (
            supabase
            .table("rounds")
            .select("id")
            .eq("tee_time_id", tee_time_id)
            .eq("status", "in_progress")
            .maybe_single()
            .execute()
        )
    return response.data if response is not None else None


def _tee_time_id_for_player_in_round(tournament_round_id: str, player_id: str) -> str | None:
    """The tee time slot (if any) this player was grouped into for a given
    tournament round -- used by _first_unfinished_prior_round_number to
    check whether an *earlier* round is done before letting them start a
    later one. Two-step (tee times in the round, then that player's
    membership among them) since tournament_tee_time_players only carries
    tee_time_id, not tournament_round_id directly."""
    with _timed(f"select tee times for tournament round {tournament_round_id}"):
        tee_times_response = (
            supabase
            .table("tournament_tee_times")
            .select("id")
            .eq("tournament_round_id", tournament_round_id)
            .execute()
        )
    tee_time_ids = [row["id"] for row in (tee_times_response.data or [])]
    if not tee_time_ids:
        return None

    with _timed(f"select grouping membership for player {player_id} in round {tournament_round_id}"):
        membership_response = (
            supabase
            .table("tournament_tee_time_players")
            .select("tee_time_id")
            .in_("tee_time_id", tee_time_ids)
            .eq("player_id", player_id)
            .maybe_single()
            .execute()
        )
    membership = membership_response.data if membership_response is not None else None
    return membership["tee_time_id"] if membership else None


def _first_unfinished_prior_round_number(
    tournament_id: str, target_round_number: int, player_id: str
) -> int | None:
    """Tournament rounds are played in order -- this walks every earlier
    round_number in the same tournament (ascending) and returns the first
    one the player was grouped into but hasn't completed, or None if there
    isn't one. A player who was never grouped into a given earlier round at
    all (joined the field late, say, or it's a single-round event) isn't
    blocked by it -- only a round they were actually assigned to and left
    unfinished counts. An earlier round sitting in pending_signoff still
    counts as unfinished here -- status != 'completed' covers both
    in_progress and pending_signoff the same way, which is exactly what
    "can't progress to the next round until everyone has signed off on the
    previous round" needs; nothing changed here to get that, the existing
    check already does it. Mirrored on the frontend by tournament.py's
    _first_unfinished_prior_round, which uses this same logic against
    already-fetched tournament data to disable/explain the Start Live
    Round button before the click; this copy is the real, authoritative
    gate."""
    with _timed(f"select earlier rounds for tournament {tournament_id} before round {target_round_number}"):
        rounds_response = (
            supabase
            .table("tournament_rounds")
            .select("id, round_number")
            .eq("tournament_id", tournament_id)
            .lt("round_number", target_round_number)
            .order("round_number")
            .execute()
        )
    earlier_rounds = rounds_response.data or []

    for earlier_round in earlier_rounds:
        tee_time_id = _tee_time_id_for_player_in_round(earlier_round["id"], player_id)
        if not tee_time_id:
            continue

        with _timed(f"select round status for tee_time {tee_time_id}"):
            round_response = (
                supabase
                .table("rounds")
                .select("status")
                .eq("tee_time_id", tee_time_id)
                .maybe_single()
                .execute()
            )
        round_row = round_response.data if round_response is not None else None
        if not round_row or round_row["status"] != "completed":
            return earlier_round["round_number"]

    return None


def fetch_live_rounds_by_tee_time(tee_time_ids: list[str]) -> dict[str, dict]:
    """Batched (one query, not one per group) lookup used by tournament_
    tee_times.py's fetch_tee_times_by_round to attach each grouping's live
    round status -- {"id": ..., "status": "in_progress"/"pending_signoff"/
    "completed"} or absent if that grouping's never started one -- so the
    tournament page's Live Round tab can render Start/Continue/Awaiting
    Sign-off/Finished without a separate round-trip per grouping.
    Deliberately doesn't filter by status; a finished tournament round
    should still show as "Finished" rather than disappearing and looking
    like it was never played."""
    if not tee_time_ids:
        return {}

    with _timed(f"select rounds for {len(tee_time_ids)} tee time(s)"):
        response = (
            supabase
            .table("rounds")
            .select("id, status, tee_time_id")
            .in_("tee_time_id", tee_time_ids)
            .execute()
        )
    return {row["tee_time_id"]: {"id": row["id"], "status": row["status"]} for row in (response.data or [])}


def start_tournament_round(tee_time_id: str, player_id: str) -> dict:
    """Starts -- or joins, if a groupmate already beat them to it -- the
    one shared live round for a tournament tee time grouping. Unlike
    start_round's invite/accept dance, every player already assigned to
    the slot becomes an accepted round_players row immediately, since the
    Start Sheet has already established who's playing together; there's
    nothing left to accept. All of them are also treated as equal owners
    (is_owner=True for everyone, not just whoever tapped Start first), so
    any grouping member can Finish or Scrap the round, matching "only a
    player from the grouping" applying symmetrically to every action, not
    just scoring.

    Runs against the tournament-scoped half of _get_active_round_id_for_
    player, not the casual half, so this doesn't collide with -- or get
    blocked by -- a player's separate casual live round, if they happen to
    have one going at the same time.
    """
    tee_time = _get_tee_time(tee_time_id)
    if not tee_time:
        raise TournamentTeeTimeNotFoundError("Tee time slot not found.")

    slot_player_ids = _tee_time_player_ids(tee_time_id)
    if player_id not in slot_player_ids:
        raise NotInGroupingError("Only a player in this group can start its live round.")

    tournament_round = _get_tournament_round(tee_time["tournament_round_id"])
    if not tournament_round:
        raise TournamentTeeTimeNotFoundError("Tournament round not found.")

    # Tournament rounds have to be played in order -- checked before the
    # existing-in-progress lookup below too, so this also applies to the
    # (rare) case of two grouping members racing to start/join the same
    # slot at once, not just a fresh start.
    blocking_round_number = _first_unfinished_prior_round_number(
        tournament_round["tournament_id"], tournament_round["round_number"], player_id
    )
    if blocking_round_number is not None:
        raise EarlierRoundNotFinishedError(blocking_round_number)

    # Already started by a groupmate -- everyone in the slot was already
    # made an accepted participant the first time this ran for it, so
    # there's nothing to do but hand back the existing round.
    existing_row = _find_in_progress_round_for_tee_time(tee_time_id)
    if existing_row:
        return get_round(existing_row["id"], viewer_player_id=player_id)

    if _get_active_round_id_for_player(player_id, tournament_scope=True):
        raise RoundAlreadyActiveError(
            "You already have a different live tournament round in progress. Finish or scrap it first."
        )

    round_payload = {
        "player_id": player_id,
        "course_id": tournament_round["course_id"],
        "tee_id": tournament_round["tee_id"],
        "is_manual": False,
        "tournament_round_id": tournament_round["id"],
        "tee_time_id": tee_time_id,
    }

    with _timed("insert rounds row (tournament)"):
        try:
            round_row = supabase.table("rounds").insert(round_payload).execute().data[0]
        except Exception as exc:
            if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                # Lost the race to a groupmate's near-simultaneous Start
                # tap -- join whatever they just created instead of
                # erroring.
                existing_row = _find_in_progress_round_for_tee_time(tee_time_id)
                if existing_row:
                    return get_round(existing_row["id"], viewer_player_id=player_id)
            raise

    round_id = round_row["id"]
    now = datetime.now(timezone.utc).isoformat()

    round_player_rows = [
        {"round_id": round_id, "player_id": pid, "is_owner": True, "status": "accepted", "responded_at": now}
        for pid in slot_player_ids
    ]
    with _timed(f"insert round_players for tournament round {round_id}"):
        supabase.table("round_players").insert(round_player_rows).execute()

    placeholder_scores = [
        {"round_id": round_id, "player_id": pid, "hole_number": n}
        for pid in slot_player_ids
        for n in range(1, 19)
    ]
    with _timed(f"insert round_scores placeholders for tournament round {round_id}"):
        supabase.table("round_scores").insert(placeholder_scores).execute()

    return get_round(round_id, viewer_player_id=player_id)


def update_hole_score(
    round_id: str, player_id: str, hole_number: int, updates: dict, requesting_player_id: str
) -> dict | None:
    # Being able to see a round_id/player_id/hole_number (e.g. from a URL)
    # isn't enough on its own -- the person making *this* request has to
    # actually be an accepted participant in the round, same as everyone
    # whose scores they're allowed to touch. This is what "only a player
    # from the grouping can upload scores" actually enforces server-side,
    # for tournament rounds and casual rounds alike -- membership already
    # decided who could BE scored (round_scores rows only exist for
    # accepted round_players), this is what decides who can DO the scoring.
    with _timed(f"check round membership round={round_id} requester={requesting_player_id}"):
        membership_response = (
            supabase
            .table("round_players")
            .select("status")
            .eq("round_id", round_id)
            .eq("player_id", requesting_player_id)
            .maybe_single()
            .execute()
        )
    membership = membership_response.data if membership_response is not None else None
    if not membership or membership["status"] != "accepted":
        raise NotRoundMemberError("Only players in this round can update its scores.")

    # A round waiting on (or already past) sign-off is meant to be a
    # frozen, submitted-for-review scorecard -- previously nothing server-
    # side stopped an accepted member from quietly editing scores while
    # others were deciding whether to approve them, only the frontend UI
    # hid the controls. Rejecting a pending sign-off resets the round back
    # to in_progress (see reject_round_signoff) and reopens it here again.
    with _timed(f"check round status for editability round={round_id}"):
        round_status_response = (
            supabase
            .table("rounds")
            .select("status")
            .eq("id", round_id)
            .maybe_single()
            .execute()
        )
    round_status_row = round_status_response.data if round_status_response is not None else None
    if not round_status_row:
        return None
    if round_status_row["status"] != "in_progress":
        raise RoundNotEditableError(
            "This round's scorecard is locked while it's awaiting sign-off (or already completed). "
            "Reject the sign-off to reopen it for edits."
        )

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
            "course_rating": round_data.get("manual_course_rating"),
            "slope_rating": round_data.get("manual_slope_rating"),
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


def delete_round(round_id: str, requesting_player_id: str | None = None) -> bool:
    """
    Used both to "scrap" a live round (abandon it without finishing) and to
    delete a completed (or pending_signoff) round from the Scoring History
    page. round_players and round_scores rows both cascade-delete via their
    round_id FK, so this is just the one table operation. Doesn't touch any
    courses/course_tees/course_holes rows a finished manual round may have
    created -- those are real course data now, independent of this one
    round.

    requesting_player_id is only enforced -- and only meaningful -- for the
    "Scrap a casual round that's still in_progress" case: only the round's
    actual creator (rounds.player_id) can do that, since it deletes the
    round for every player still in it, not just themselves (a non-creator
    accepted player who wants out uses leave_round instead, which only
    removes their own participation, leaving the round and everyone else
    in it untouched). A tournament round's Scrap stays open to any
    grouping member regardless of requesting_player_id, same as it's
    always been -- there's no single privileged creator among an equal
    grouping (see start_tournament_round). Deleting a round that's already
    past in_progress (pending_signoff or completed, from Scoring History)
    is left unrestricted too -- that's "clean up my own history", not
    "scrap", and every caller of that path already only ever sees rounds
    it itself belongs to. Passing None (as _expire_stale_rounds does, for
    its own automatic cleanup rather than a real user action) skips this
    check entirely.
    """
    if requesting_player_id is not None:
        with _timed(f"select round {round_id} for delete/scrap permission check"):
            round_response = supabase.table("rounds").select("*").eq("id", round_id).maybe_single().execute()
        round_row = round_response.data if round_response is not None else None
        if round_row and round_row["status"] == "in_progress" and not round_row.get("tournament_round_id"):
            if round_row["player_id"] != requesting_player_id:
                raise NotRoundCreatorError(
                    "Only the round's creator can scrap it -- ask them, or leave the round instead."
                )

    with _timed(f"delete round {round_id}"):
        response = supabase.table("rounds").delete().eq("id", round_id).execute()
    return bool(response.data)


def leave_round(round_id: str, player_id: str) -> None:
    """Removes a non-creator accepted player from a still-in_progress
    casual round -- the opposite of delete_round (which removes the whole
    round for everyone) and distinct from Scrap Round (creator-only, also
    removes it for everyone). See CannotLeaveRoundError/
    RoundNotInProgressError above for exactly when this isn't allowed --
    the round's own creator, a tournament round of any kind, or a round
    that's already moved past in_progress.

    Deletes this player's round_players row (and, via its own FK, their
    round_scores rows) outright -- they're simply no longer part of this
    round's history from this point on, same as if they'd never accepted
    the invite in the first place. If this drops the round to a single
    remaining accepted player, it naturally becomes a solo round from
    here with no extra work needed -- finish_round's own
    len(players) > 1 check and _gather_round_inputs' participant-count
    filter (see whs.py) already handle that correctly: it'll finish
    straight to completed with no sign-off required, and, like any solo
    round, never contribute to anyone's Handicap Index.
    """
    with _timed(f"select round {round_id} for leave"):
        round_response = supabase.table("rounds").select("*").eq("id", round_id).maybe_single().execute()
    round_row = round_response.data if round_response is not None else None
    if not round_row or round_row["status"] != "in_progress":
        raise RoundNotInProgressError("You can only leave a round that's still in progress.")

    if round_row.get("tournament_round_id"):
        raise CannotLeaveRoundError("Tournament rounds can't be left -- talk to your grouping if you can't finish.")

    if round_row["player_id"] == player_id:
        raise CannotLeaveRoundError("The round's creator can't leave it -- scrap the round instead.")

    with _timed(f"select round_player round={round_id} player={player_id} for leave"):
        rp_response = (
            supabase
            .table("round_players")
            .select("*")
            .eq("round_id", round_id)
            .eq("player_id", player_id)
            .maybe_single()
            .execute()
        )
    round_player = rp_response.data if rp_response is not None else None
    if not round_player or round_player["status"] != "accepted":
        raise NotRoundMemberError("You're not an active player in this round.")

    with _timed(f"delete round_scores for leaving player={player_id} round={round_id}"):
        supabase.table("round_scores").delete().eq("round_id", round_id).eq("player_id", player_id).execute()
    with _timed(f"delete round_players row for leaving player={player_id} round={round_id}"):
        supabase.table("round_players").delete().eq("round_id", round_id).eq("player_id", player_id).execute()


def mark_round_no_result(round_id: str, player_id: str) -> None:
    """Bulk-fills every *unscored* hole of this player's own scorecard
    within a tournament round with No Return -- a self-service withdrawal
    from the rest of their round, not the whole thing: every other
    player's scorecard, and the round itself, are completely untouched
    and keep going. Tournament rounds only (see CannotMarkNoResultError)
    -- there's no leaderboard concept for a casual round played with
    friends; Leave Round already covers "I need to step away" there (see
    leave_round). Only available while the round is still in_progress,
    same restriction and reasoning as leave_round (see RoundNotInProgress
    Error).

    Only touches holes where strokes IS NULL -- any hole this player
    already has a real score on is left exactly as entered, not
    overwritten. This is what makes "NR the rest of my round" the actual
    behavior rather than "erase everything and mark it all NR": someone
    who's played 12 holes and then can't continue keeps those 12 real
    scores, with only holes 13-18 becoming NR. The round-level is_nr flag
    the leaderboard sorts on (see _compute_leaderboard_line in backend/
    services/tournaments.py) only needs *any* hole marked NR to trigger,
    so this still reliably sends them to the bottom of the board even
    though most of their card is real.

    Individual holes marked NR this way can still be turned back into a
    real score afterward exactly like any other hole: entering strokes
    through the normal Enter Score flow just overwrites it (see
    HoleScoreUpdate's docstring in backend/models/round.py) -- there's no
    separate "undo" action needed for that, or for undoing this in bulk.
    """
    with _timed(f"select round {round_id} for no-result"):
        round_response = supabase.table("rounds").select("*").eq("id", round_id).maybe_single().execute()
    round_row = round_response.data if round_response is not None else None
    if not round_row or round_row["status"] != "in_progress":
        raise RoundNotInProgressError("You can only mark No Result while the round is still in progress.")

    if not round_row.get("tournament_round_id"):
        raise CannotMarkNoResultError("No Result only applies to tournament rounds.")

    with _timed(f"select round_player round={round_id} player={player_id} for no-result"):
        rp_response = (
            supabase
            .table("round_players")
            .select("*")
            .eq("round_id", round_id)
            .eq("player_id", player_id)
            .maybe_single()
            .execute()
        )
    round_player = rp_response.data if rp_response is not None else None
    if not round_player or round_player["status"] != "accepted":
        raise NotRoundMemberError("You're not an active player in this round.")

    with _timed(f"mark unscored holes NR for player={player_id} round={round_id}"):
        supabase.table("round_scores").update(
            {"nr": True, "strokes": None, "putts": None, "fairway_hit": None}
        ).eq("round_id", round_id).eq("player_id", player_id).is_("strokes", "null").execute()


def finish_round(round_id: str, requesting_player_id: str) -> dict | None:
    round_data = get_round(round_id)
    if not round_data:
        return None

    # Idempotent no-op if this round's already past in_progress -- the
    # frontend only shows Finish on a live round, but nothing stops a
    # slow double-tap/retry reaching this twice, and blindly re-running
    # the block below a second time would reset completed_at and could
    # re-trigger handicap recalculation needlessly. Checked before the
    # membership check below on purpose -- a stale double-tap from
    # someone who's since left the round shouldn't error just because
    # there's nothing left to do anyway.
    if round_data["status"] != "in_progress":
        return round_data

    # Both players involved (not just the round's creator) can finish it
    # now -- Finish used to be shown only to is_owner_of_round on the
    # frontend (see components/live_scorecard.py), which for a casual
    # round meant only its creator; this is what makes that actually true
    # server-side too, not just a UI change, the same way update_hole_
    # score/sign_off_round/reject_round_signoff already gate on real
    # round membership rather than trusting whoever calls this with a
    # round_id. Tournament rounds already worked this way in effect
    # (every grouping member is_owner=True), this just makes it explicit
    # and enforced for casual rounds too.
    if not any(p["player_id"] == requesting_player_id for p in round_data["players"]):
        raise NotRoundMemberError("Only players in this round can finish it.")

    if round_data["is_manual"] and not round_data.get("tee_id"):
        owner_entry = next((p for p in round_data["players"] if p["is_owner"]), None)
        if owner_entry:
            _create_course_from_manual_entry(round_data, owner_entry["holes"])
            round_data = get_round(round_id)

    # A round with more than one accepted player -- a casual round played
    # with friends, or any tournament round (always multi-player, see
    # start_tournament_round) -- isn't final the moment the last hole's
    # entered; every player involved has to sign off on the scorecard
    # first (see sign_off_round below). It still moves out of in_progress
    # right here, and completed_at is still set now too -- that's when
    # play actually finished; sign-off arriving later doesn't change
    # *when it was played*, only when it's accepted -- it just lands on
    # pending_signoff instead of completed. A solo round has nobody else
    # who needs to approve it, so it goes straight to completed -- but
    # that's the only thing finishing immediately does for it. A solo
    # round never contributes to anyone's Handicap Index at all, at any
    # point -- see _gather_round_inputs in whs.py, which excludes any
    # round with only one accepted participant outright, regardless of
    # status. Only a round played with other people counts, and only
    # once it's genuinely completed (which, for a multiplayer round,
    # means fully signed off).
    is_multiplayer = len(round_data["players"]) > 1
    new_status = "pending_signoff" if is_multiplayer else "completed"

    with _timed(f"mark round {round_id} {new_status}"):
        supabase.table("rounds").update({
            "status": new_status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", round_id).execute()

    # A solo round has nobody else who needs to sign off, so it reaches
    # status=completed right here rather than at the end of sign_off_round
    # below -- this is the only place that ever happens for a solo round,
    # so this is also the only place its feed post can be created.
    # Best-effort, same convention as sign_off_round's own call to this:
    # a feed post failing should never be able to block the round itself
    # from finishing.
    if not is_multiplayer:
        try:
            from backend.services.round_posts import create_round_post
            create_round_post(round_id, [p["player_id"] for p in round_data["players"]])
        except Exception as exc:
            print(f"[FEED] Failed to create round post for round={round_id}: {exc}")

    # Everyone else in a multiplayer round still needs to sign off before
    # it's actually done -- best-effort, same convention as every other
    # create_notification call site, a notification failing to write
    # should never be able to block the round itself from finishing.
    if is_multiplayer:
        finisher = next((p for p in round_data["players"] if p["player_id"] == requesting_player_id), None)
        finisher_name = (finisher or {}).get("nickname") or (finisher or {}).get("first_name") or "A player"
        for p in round_data["players"]:
            if p["player_id"] == requesting_player_id:
                continue
            try:
                create_notification(
                    p["player_id"],
                    "play",
                    f"{finisher_name} finished your round -- sign off on the scorecard",
                    url="/round-signoff",
                )
            except Exception as exc:
                print(f"[NOTIFY] Failed to notify {p['player_id']} of round {round_id} needing sign-off: {exc}")

    # No recalculation triggered here for either branch. A solo round is
    # permanently excluded from _gather_round_inputs, so recalculating
    # right after one finishes would just recompute the exact same
    # Handicap Index from the same eligible (multiplayer) rounds as
    # before -- work for no possible change. A multiplayer round's
    # players get recalculated later, once every player has signed off
    # (see sign_off_round below) -- that round doesn't even reach
    # status='completed' until then, so there's nothing for
    # _gather_round_inputs to pick up yet regardless.

    return get_round(round_id)


def sign_off_round(round_id: str, player_id: str) -> dict:
    """Records this player's approval of a pending_signoff round's final
    scorecard. Once every accepted player has signed off, the round
    itself flips to completed (completed_at is left as whenever play
    actually finished, set back in finish_round -- not touched again
    here) and every accepted player's Handicap Index is recalculated for
    the first time from this round -- mirrors finish_round's own
    best-effort recalc loop for the solo case, just deferred to this
    later point instead of running immediately."""
    with _timed(f"select round {round_id} for signoff"):
        round_response = supabase.table("rounds").select("*").eq("id", round_id).maybe_single().execute()
    round_row = round_response.data if round_response is not None else None
    if not round_row or round_row["status"] != "pending_signoff":
        raise RoundNotPendingSignoffError("This round isn't currently awaiting sign-off.")

    with _timed(f"select round_player round={round_id} player={player_id} for signoff"):
        rp_response = (
            supabase
            .table("round_players")
            .select("*")
            .eq("round_id", round_id)
            .eq("player_id", player_id)
            .maybe_single()
            .execute()
        )
    round_player = rp_response.data if rp_response is not None else None
    if not round_player or round_player["status"] != "accepted":
        raise NotRoundMemberError("Only players in this round can sign off on it.")

    now = datetime.now(timezone.utc).isoformat()
    with _timed(f"record signoff round={round_id} player={player_id}"):
        supabase.table("round_players").update(
            {"signed_off_at": now}
        ).eq("round_id", round_id).eq("player_id", player_id).execute()

    with _timed(f"check outstanding signoffs for round {round_id}"):
        outstanding_response = (
            supabase
            .table("round_players")
            .select("player_id")
            .eq("round_id", round_id)
            .eq("status", "accepted")
            .is_("signed_off_at", "null")
            .execute()
        )
    still_pending = outstanding_response.data or []

    if not still_pending:
        with _timed(f"mark round {round_id} completed (all signed off)"):
            supabase.table("rounds").update({"status": "completed"}).eq("id", round_id).execute()

        with _timed(f"select accepted players for round {round_id} to recalculate handicaps"):
            accepted_response = (
                supabase
                .table("round_players")
                .select("player_id")
                .eq("round_id", round_id)
                .eq("status", "accepted")
                .execute()
            )
        accepted_player_ids = [row["player_id"] for row in (accepted_response.data or [])]
        # Captured per-player so create_round_post below can show each
        # player their own before/after Handicap Index change on the
        # feed card (see round_posts.py's viewer_handicap_change). Read
        # BEFORE recalculating, not after -- get_current_player_handicap
        # would otherwise just return the same freshly-written row
        # recalculate_and_store_handicap itself just inserted, making
        # every change look like zero.
        handicap_changes = {}
        for pid in accepted_player_ids:
            try:
                before = get_current_player_handicap(pid)
                before_value = before.get("handicap") if before else None
                after = recalculate_and_store_handicap(pid)
                after_value = after.get("handicap") if after else before_value
                if before_value is not None and after_value is not None:
                    handicap_changes[pid] = round(after_value - before_value, 1)
            except Exception as exc:
                print(f"[WHS] Failed to recalculate handicap for player {pid}: {exc}")

        # Best-effort, same reasoning as every other automated feed post
        # hook (join/tournament) -- a feed post failing should never be
        # able to block a round actually completing. Only fires here (not
        # in finish_round's multiplayer branch) because a multiplayer
        # round never reaches status=completed anywhere else.
        #
        # BUG FIX: this used to import create_scorecard_posts from
        # backend.services.club_posts -- the OLD scorecard-post path,
        # retired in favor of round_posts.create_round_post (see that
        # module's own docstring, which already describes this exact
        # call site as if it existed). create_scorecard_posts no longer
        # exists in club_posts.py at all, so this import has been
        # silently raising ImportError and getting swallowed by the
        # except below on every single sign-off, for every multiplayer
        # round, since the retirement landed -- rounds completed and
        # handicaps recalculated exactly as expected, but no feed post
        # was ever created, with nothing visible anywhere to say so.
        # Calling the actual current function instead.
        try:
            from backend.services.round_posts import create_round_post
            create_round_post(round_id, accepted_player_ids, handicap_changes=handicap_changes)
        except Exception as exc:
            print(f"[FEED] Failed to create round post for round={round_id}: {exc}")

    return get_round(round_id, viewer_player_id=player_id)


def reject_round_signoff(round_id: str, player_id: str) -> dict:
    """Sends a pending_signoff round back for edits -- resets it to
    in_progress (which naturally reopens it to update_hole_score again,
    see RoundNotEditableError above) and clears every accepted player's
    signed_off_at, not just the rejecting player's. The scorecard is
    about to change, so anyone who already approved it needs to look at
    it again once it's resubmitted -- their earlier approval was of a
    version that's now being edited."""
    with _timed(f"select round {round_id} for signoff rejection"):
        round_response = supabase.table("rounds").select("*").eq("id", round_id).maybe_single().execute()
    round_row = round_response.data if round_response is not None else None
    if not round_row or round_row["status"] != "pending_signoff":
        raise RoundNotPendingSignoffError("This round isn't currently awaiting sign-off.")

    with _timed(f"select round_player round={round_id} player={player_id} for signoff rejection"):
        rp_response = (
            supabase
            .table("round_players")
            .select("*")
            .eq("round_id", round_id)
            .eq("player_id", player_id)
            .maybe_single()
            .execute()
        )
    round_player = rp_response.data if rp_response is not None else None
    if not round_player or round_player["status"] != "accepted":
        raise NotRoundMemberError("Only players in this round can reject its sign-off.")

    with _timed(f"reopen round {round_id} to in_progress (rejected)"):
        supabase.table("rounds").update({"status": "in_progress"}).eq("id", round_id).execute()

    with _timed(f"clear signoffs for round {round_id}"):
        supabase.table("round_players").update(
            {"signed_off_at": None}
        ).eq("round_id", round_id).eq("status", "accepted").execute()

    updated_round = get_round(round_id, viewer_player_id=player_id)

    # Best-effort, same convention as every other create_notification call
    # site -- everyone else who'd already signed off needs to know their
    # earlier approval no longer counts, since the scorecard they approved
    # is about to change.
    rejecter = next((p for p in updated_round["players"] if p["player_id"] == player_id), None)
    rejecter_name = (rejecter or {}).get("nickname") or (rejecter or {}).get("first_name") or "A player"
    for p in updated_round["players"]:
        if p["player_id"] == player_id:
            continue
        try:
            create_notification(
                p["player_id"],
                "play",
                f"{rejecter_name} sent your round back for edits",
                url="/play",
            )
        except Exception as exc:
            print(f"[NOTIFY] Failed to notify {p['player_id']} of round {round_id} sign-off rejection: {exc}")

    return updated_round


def list_pending_signoff_rounds(player_id: str) -> list[dict]:
    """Every pending_signoff round this player is an accepted participant
    in and hasn't signed off on yet -- what powers the navbar notification
    pill's count and the dedicated review panel's list. A round this
    player already signed off on (just waiting on someone else) isn't
    included -- there's nothing left for this player to do on those."""
    with _timed(f"select unsigned accepted round_players for player {player_id}"):
        rp_response = (
            supabase
            .table("round_players")
            .select("round_id")
            .eq("player_id", player_id)
            .eq("status", "accepted")
            .is_("signed_off_at", "null")
            .execute()
        )
    round_ids = [r["round_id"] for r in (rp_response.data or [])]
    if not round_ids:
        return []

    with _timed(f"select pending_signoff rounds among player {player_id}'s rounds"):
        rounds_response = (
            supabase
            .table("rounds")
            .select("*")
            .in_("id", round_ids)
            .eq("status", "pending_signoff")
            .order("completed_at", desc=True)
            .execute()
        )
    round_rows = rounds_response.data or []

    hydrated_rounds = []
    for round_row in round_rows:
        hydrated = _hydrate_round(round_row)
        _apply_viewer_is_owner(hydrated, round_row, player_id)
        hydrated_rounds.append(hydrated)
    return hydrated_rounds