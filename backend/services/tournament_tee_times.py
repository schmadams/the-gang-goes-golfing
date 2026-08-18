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