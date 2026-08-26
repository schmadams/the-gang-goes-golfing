# target path: backend/services/tournament_tee_times.py (new file)
import random
from datetime import date as date_cls
from datetime import datetime, timedelta

from backend.database import supabase
from backend.models.tournament import TeeTimeAssignmentRequest, TeeTimeGenerateRequest, TeeTimeUpdateRequest
from backend.services.rounds import fetch_live_rounds_by_tee_time

_TEE_TIME_INTERVAL_MINUTES = 8
_PLAYER_EMBED = "players(id, first_name, surname, nickname)"


class RoundNotFoundError(Exception):
    """Raised when round_id doesn't match any tournament_rounds row (or its
    parent tournament has gone missing, which shouldn't happen given the
    FK, but is checked defensively anyway)."""


class NotClubAdminError(Exception):
    """Raised if someone other than the tournament's club admin tries to
    generate tee times -- same restriction as editing/updating the
    tournament itself."""


class NoConfirmedEntrantsError(Exception):
    """Raised when there are no confirmed entrants to group -- nothing to
    generate tee times for yet."""


class NoTeeTimeSlotsError(Exception):
    """Raised when trying to assign players before any tee time slots
    exist for the round -- generate slots first."""


class InvalidTeeTimeSlotError(Exception):
    """Raised when an assignment names a tee_time_id that isn't one of this
    round's own slots (either a stale id from before a regenerate, or one
    belonging to a different round entirely)."""


class TeeTimeSlotNotFoundError(Exception):
    """Raised when tee_time_id doesn't match any tournament_tee_times row."""


def _get_round(round_id: str) -> dict | None:
    response = supabase.table("tournament_rounds").select("*").eq("id", round_id).maybe_single().execute()
    return response.data if response is not None else None


def _get_tournament(tournament_id: str) -> dict | None:
    response = supabase.table("tournaments").select("*").eq("id", tournament_id).maybe_single().execute()
    return response.data if response is not None else None


def _get_club(club_id: str) -> dict | None:
    response = supabase.table("clubs").select("*").eq("id", club_id).maybe_single().execute()
    return response.data if response is not None else None


def _confirmed_entrants(tournament_id: str) -> list[dict]:
    response = (
        supabase
        .table("tournament_entrants")
        .select(f"*, {_PLAYER_EMBED}")
        .eq("tournament_id", tournament_id)
        .eq("status", "confirmed")
        .order("created_at")
        .execute()
    )
    entrants = []
    for e in (response.data or []):
        player = e.pop("players", None) or {}
        entrants.append({
            **e,
            "first_name": player.get("first_name"),
            "surname": player.get("surname"),
            "nickname": player.get("nickname"),
        })
    return entrants


def _order_entrants(entrants: list[dict], grouping_method: str) -> list[dict]:
    if grouping_method == "handicap":
        # Ascending -- lowest (best) handicap tees off first. None (no
        # handicap on file) sorts last rather than raising, since None
        # can't be compared against a float directly.
        return sorted(
            entrants,
            key=lambda e: (e["handicap_at_entry"] is None, e["handicap_at_entry"]),
        )
    shuffled = list(entrants)
    random.shuffle(shuffled)
    return shuffled


def _chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def generate_tee_times(round_id: str, payload: TeeTimeGenerateRequest) -> list[dict]:
    """Wholesale regenerate -- same "replace, don't diff" approach
    update_tournament already uses for tournament_rounds (see that
    function's comment). Anytime an admin hits Generate, whatever's there
    gets thrown out and rebuilt from the current confirmed-entrant list, so
    a withdrawal/late add is handled by just running it again."""
    round_row = _get_round(round_id)
    if not round_row:
        raise RoundNotFoundError("Round not found.")

    tournament = _get_tournament(round_row["tournament_id"])
    if not tournament:
        raise RoundNotFoundError("Round not found.")

    club = _get_club(tournament["club_id"])
    if not club or str(club.get("club_admin")) != str(payload.admin_id):
        raise NotClubAdminError("Only this club's admin can generate tee times.")

    entrants = _confirmed_entrants(tournament["id"])
    if not entrants:
        raise NoConfirmedEntrantsError("No confirmed entrants to schedule yet.")

    group_size = round_row.get("group_size") or 4
    grouping_method = tournament.get("grouping_method", "random")

    if grouping_method == "manual":
        # No auto-assignment -- just enough empty slots to hold every
        # confirmed entrant (same count math as the auto methods' chunking
        # would produce), which the admin then fills in themselves via
        # assign_tee_time_players. Empty lists here flow straight through
        # the same insert logic below as a real group would, they just
        # produce zero player_rows for that slot.
        num_groups = -(-len(entrants) // group_size)  # ceil division
        groups = [[] for _ in range(num_groups)]
    else:
        ordered = _order_entrants(entrants, grouping_method)
        groups = _chunk(ordered, group_size)

    supabase.table("tournament_tee_times").delete().eq("tournament_round_id", round_id).execute()

    base_dt = datetime.combine(date_cls.today(), payload.first_tee_time)
    tee_time_rows = [
        {
            "tournament_round_id": round_id,
            "group_number": index + 1,
            "tee_time": (base_dt + timedelta(minutes=_TEE_TIME_INTERVAL_MINUTES * index)).time().isoformat(),
        }
        for index in range(len(groups))
    ]
    tee_times_response = supabase.table("tournament_tee_times").insert(tee_time_rows).execute()

    # Match inserted rows back to groups by group_number (a value we set
    # ourselves) rather than assuming the insert response preserves the
    # order it was sent in -- safer than zipping the two lists directly.
    id_by_group_number = {r["group_number"]: r["id"] for r in (tee_times_response.data or [])}

    player_rows = []
    for index, group in enumerate(groups):
        tee_time_id = id_by_group_number.get(index + 1)
        for entrant in group:
            player_rows.append({"tee_time_id": tee_time_id, "player_id": entrant["player_id"]})
    if player_rows:
        supabase.table("tournament_tee_time_players").insert(player_rows).execute()

    return fetch_tee_times_by_round([round_id]).get(round_id, [])


def assign_tee_time_players(round_id: str, payload: TeeTimeAssignmentRequest) -> list[dict]:
    """Manual-mode counterpart to generate_tee_times -- takes the full set
    of player_id -> tee_time_id (or None) assignments from the Start Sheet
    tab and replaces whatever's currently assigned wholesale, same
    "replace, don't diff" approach as everywhere else in this file. Doesn't
    care what the tournament's grouping_method actually is; an admin
    reassigning a player after an auto-generate would work exactly the
    same way, it's just that the Start Sheet UI only ever shows these
    controls when grouping_method is "manual"."""
    round_row = _get_round(round_id)
    if not round_row:
        raise RoundNotFoundError("Round not found.")

    tournament = _get_tournament(round_row["tournament_id"])
    if not tournament:
        raise RoundNotFoundError("Round not found.")

    club = _get_club(tournament["club_id"])
    if not club or str(club.get("club_admin")) != str(payload.admin_id):
        raise NotClubAdminError("Only this club's admin can assign tee times.")

    existing_slots_response = (
        supabase.table("tournament_tee_times").select("id").eq("tournament_round_id", round_id).execute()
    )
    valid_slot_ids = {row["id"] for row in (existing_slots_response.data or [])}
    if not valid_slot_ids:
        raise NoTeeTimeSlotsError("Generate tee time slots for this round first.")

    for tee_time_id in payload.assignments.values():
        if tee_time_id is not None and tee_time_id not in valid_slot_ids:
            raise InvalidTeeTimeSlotError("That tee time slot doesn't belong to this round.")

    supabase.table("tournament_tee_time_players").delete().in_("tee_time_id", list(valid_slot_ids)).execute()

    player_rows = [
        {"tee_time_id": tee_time_id, "player_id": player_id}
        for player_id, tee_time_id in payload.assignments.items()
        if tee_time_id is not None
    ]
    if player_rows:
        supabase.table("tournament_tee_time_players").insert(player_rows).execute()

    return fetch_tee_times_by_round([round_id]).get(round_id, [])


def update_tee_time_slot(tee_time_id: str, payload: TeeTimeUpdateRequest) -> list[dict]:
    """One-off override for a single slot -- separate from generate_tee_
    times' wholesale regenerate, since nudging one group's tee time later
    (a slow group ahead, a course closure on one hole, whatever) shouldn't
    force throwing out and rebuilding every other slot and assignment for
    the round. Doesn't reorder anything -- the Start Sheet still lists
    slots by group_number, not by tee_time, so an edited time can end up
    out of chronological order in the list if that's what the admin
    actually wants (e.g. swapping two groups' start times)."""
    slot_response = supabase.table("tournament_tee_times").select("*").eq("id", tee_time_id).maybe_single().execute()
    slot = slot_response.data if slot_response is not None else None
    if not slot:
        raise TeeTimeSlotNotFoundError("Tee time slot not found.")

    round_row = _get_round(slot["tournament_round_id"])
    if not round_row:
        raise RoundNotFoundError("Round not found.")

    tournament = _get_tournament(round_row["tournament_id"])
    if not tournament:
        raise RoundNotFoundError("Round not found.")

    club = _get_club(tournament["club_id"])
    if not club or str(club.get("club_admin")) != str(payload.admin_id):
        raise NotClubAdminError("Only this club's admin can edit tee times.")

    supabase.table("tournament_tee_times").update(
        {"tee_time": payload.tee_time.isoformat()}
    ).eq("id", tee_time_id).execute()

    return fetch_tee_times_by_round([round_row["id"]]).get(round_row["id"], [])


def list_scheduled_tee_times_for_player(player_id: str) -> list[dict]:
    """Every tee time slot this player is grouped into, across every
    tournament in every club they belong to, that hasn't been started yet
    -- powers the Play page's Scheduled tab. A slot drops off this list
    the moment its round is started (see fetch_live_rounds_by_tee_time --
    once a live_round exists for a slot it belongs on the Live tab
    instead, in progress or not), so this is genuinely "what's still
    ahead of you", not just "every grouping you're in".

    Deliberately a flat cross-tournament/cross-club query rather than
    "for each club you're in, for each tournament, for each round..." --
    tournament_tee_time_players already has this player's own rows
    directly, so starting from there and joining outward (tee time ->
    round -> tournament -> club) is one pass instead of N+1 fan-out."""
    membership_response = (
        supabase
        .table("tournament_tee_time_players")
        .select("tee_time_id")
        .eq("player_id", player_id)
        .execute()
    )
    tee_time_ids = list({row["tee_time_id"] for row in (membership_response.data or [])})
    if not tee_time_ids:
        return []

    tee_times_response = (
        supabase.table("tournament_tee_times").select("*").in_("id", tee_time_ids).execute()
    )
    tee_times = tee_times_response.data or []
    if not tee_times:
        return []

    live_rounds_by_tee_time = fetch_live_rounds_by_tee_time([t["id"] for t in tee_times])
    tee_times = [t for t in tee_times if not live_rounds_by_tee_time.get(t["id"])]
    if not tee_times:
        return []

    round_ids = list({t["tournament_round_id"] for t in tee_times})
    rounds_response = supabase.table("tournament_rounds").select("*").in_("id", round_ids).execute()
    rounds_by_id = {r["id"]: r for r in (rounds_response.data or [])}

    course_ids = list({r["course_id"] for r in rounds_by_id.values() if r.get("course_id")})
    courses_by_id = {}
    if course_ids:
        courses_response = (
            supabase.table("courses").select("id, club_name, course_name").in_("id", course_ids).execute()
        )
        courses_by_id = {c["id"]: c for c in (courses_response.data or [])}

    tee_ids = list({r["tee_id"] for r in rounds_by_id.values() if r.get("tee_id")})
    tees_by_id = {}
    if tee_ids:
        tees_response = supabase.table("course_tees").select("id, name").in_("id", tee_ids).execute()
        tees_by_id = {t["id"]: t for t in (tees_response.data or [])}

    tournament_ids = list({r["tournament_id"] for r in rounds_by_id.values()})
    tournaments_response = supabase.table("tournaments").select("*").in_("id", tournament_ids).execute()
    tournaments_by_id = {t["id"]: t for t in (tournaments_response.data or [])}

    club_ids = list({t["club_id"] for t in tournaments_by_id.values()})
    clubs_response = supabase.table("clubs").select("id, name, slug").in_("id", club_ids).execute()
    clubs_by_id = {c["id"]: c for c in (clubs_response.data or [])}

    players_response = (
        supabase
        .table("tournament_tee_time_players")
        .select(f"*, {_PLAYER_EMBED}")
        .in_("tee_time_id", [t["id"] for t in tee_times])
        .execute()
    )
    players_by_tee_time: dict[str, list[dict]] = {}
    for row in (players_response.data or []):
        player = row.pop("players", None) or {}
        players_by_tee_time.setdefault(row["tee_time_id"], []).append({
            "player_id": row["player_id"],
            "first_name": player.get("first_name"),
            "surname": player.get("surname"),
            "nickname": player.get("nickname"),
        })

    scheduled = []
    for t in tee_times:
        tournament_round = rounds_by_id.get(t["tournament_round_id"]) or {}
        tournament = tournaments_by_id.get(tournament_round.get("tournament_id")) or {}
        club = clubs_by_id.get(tournament.get("club_id")) or {}
        course = courses_by_id.get(tournament_round.get("course_id")) or {}
        tee = tees_by_id.get(tournament_round.get("tee_id")) or {}
        scheduled.append({
            "tee_time_id": t["id"],
            "tee_time": t["tee_time"],
            "group_number": t["group_number"],
            "round_date": tournament_round.get("round_date"),
            "round_number": tournament_round.get("round_number"),
            "tournament_id": tournament.get("id"),
            "tournament_name": tournament.get("name"),
            "club_id": club.get("id"),
            "club_name": club.get("name"),
            "club_slug": club.get("slug"),
            "venue_name": course.get("club_name"),
            "course_name": course.get("course_name"),
            "tee_name": tee.get("name"),
            "players": players_by_tee_time.get(t["id"], []),
        })

    # Soonest first -- round_date then tee_time, both plain
    # date/time-ish strings that sort correctly lexicographically in
    # ISO format. Missing values sort first rather than raising.
    scheduled.sort(key=lambda s: (s["round_date"] or "", s["tee_time"] or ""))
    return scheduled


def fetch_tee_times_by_round(round_ids: list[str]) -> dict[str, list[dict]]:
    """Batched the same way tournaments.py's _fetch_rounds_by_tournament /
    _fetch_entrants_by_tournament are -- one query for every round in the
    batch rather than one per round, since this gets called on every
    tournament detail load, not just after a generate."""
    if not round_ids:
        return {}

    tee_times_response = (
        supabase
        .table("tournament_tee_times")
        .select("*")
        .in_("tournament_round_id", round_ids)
        .order("group_number")
        .execute()
    )
    tee_times = tee_times_response.data or []
    if not tee_times:
        return {}

    tee_time_ids = [t["id"] for t in tee_times]
    players_response = (
        supabase
        .table("tournament_tee_time_players")
        .select(f"*, {_PLAYER_EMBED}")
        .in_("tee_time_id", tee_time_ids)
        .execute()
    )
    players_by_tee_time: dict[str, list[dict]] = {}
    for row in (players_response.data or []):
        player = row.pop("players", None) or {}
        players_by_tee_time.setdefault(row["tee_time_id"], []).append({
            "player_id": row["player_id"],
            "first_name": player.get("first_name"),
            "surname": player.get("surname"),
            "nickname": player.get("nickname"),
        })

    # Attaches each slot's live round status (if it's ever had one started)
    # so the tournament page's Live Round tab can render Start/Continue/
    # Finished straight from the same tournament detail fetch every other
    # tab already uses, instead of a separate round-trip per grouping.
    live_rounds_by_tee_time = fetch_live_rounds_by_tee_time(tee_time_ids)

    grouped: dict[str, list[dict]] = {}
    for t in tee_times:
        grouped.setdefault(t["tournament_round_id"], []).append({
            "id": t["id"],
            "group_number": t["group_number"],
            "tee_time": t["tee_time"],
            "players": players_by_tee_time.get(t["id"], []),
            "live_round": live_rounds_by_tee_time.get(t["id"]),
        })
    return grouped