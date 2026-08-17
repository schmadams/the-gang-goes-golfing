# target path: backend/services/tournament_tee_times.py (new file)
import random
from datetime import date as date_cls
from datetime import datetime, timedelta

from backend.database import supabase
from backend.models.tournament import TeeTimeGenerateRequest

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

    ordered = _order_entrants(entrants, tournament.get("grouping_method", "random"))
    groups = _chunk(ordered, round_row.get("group_size") or 4)

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

    grouped: dict[str, list[dict]] = {}
    for t in tee_times:
        grouped.setdefault(t["tournament_round_id"], []).append({
            "id": t["id"],
            "group_number": t["group_number"],
            "tee_time": t["tee_time"],
            "players": players_by_tee_time.get(t["id"], []),
        })
    return grouped