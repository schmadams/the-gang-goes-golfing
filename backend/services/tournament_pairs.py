# target path: backend/services/tournament_pairs.py (new file)
from backend.database import supabase

_PLAYER_A_EMBED = "player_a:players!tournament_pairs_player_id_a_fkey(id, first_name, surname, nickname)"
_PLAYER_B_EMBED = "player_b:players!tournament_pairs_player_id_b_fkey(id, first_name, surname, nickname)"


class TournamentNotFoundError(Exception):
    """Raised when tournament_id doesn't match any tournament."""


class NotClubAdminError(Exception):
    """Raised if someone other than the tournament's club admin tries to
    set its pairings."""


class InvalidPairingError(Exception):
    """Raised when a submitted set of pairs breaks one of the rules below
    -- a player paired with themselves, a player appearing in more than
    one pair, or a player who isn't a confirmed entrant of whichever
    roster this tournament resolves to (see tournaments.
    linked_tournament_id -- a linked pairs tournament shares its entrant
    list with the tournament it's linked to, so pairings are validated
    against that shared list, not some roster of the pairs tournament's
    own, which never has one)."""


def _get_tournament(tournament_id: str) -> dict | None:
    response = supabase.table("tournaments").select("*").eq("id", tournament_id).maybe_single().execute()
    return response.data if response is not None else None


def _get_club(club_id: str) -> dict | None:
    response = supabase.table("clubs").select("*").eq("id", club_id).maybe_single().execute()
    return response.data if response is not None else None


def _confirmed_entrant_ids(tournament: dict) -> set[str]:
    """The pool of player_ids pairings are validated against -- the
    *shared* roster a linked pairs tournament resolves to (see
    tournaments.linked_tournament_id), or this tournament's own confirmed
    entrants if it isn't linked to anything."""
    roster_tournament_id = tournament.get("linked_tournament_id") or tournament["id"]
    response = (
        supabase
        .table("tournament_entrants")
        .select("player_id")
        .eq("tournament_id", roster_tournament_id)
        .eq("status", "confirmed")
        .execute()
    )
    return {row["player_id"] for row in (response.data or [])}


def _name(player: dict) -> str | None:
    return player.get("nickname") or f"{player.get('first_name', '')} {player.get('surname', '')}".strip() or None


def _flatten_pair(pair: dict) -> dict:
    player_a = pair.pop("player_a", None) or {}
    player_b = pair.pop("player_b", None) or {}
    return {**pair, "player_a_name": _name(player_a), "player_b_name": _name(player_b)}


def list_tournament_pairs(tournament_id: str) -> list[dict]:
    """Pairings belong to the pairs competition's own tournament_id, not
    resolved through linked_tournament_id -- see the migration comment on
    tournament_pairs.tournament_id. Only who's *eligible* to be paired
    (the entrant pool, in _confirmed_entrant_ids) is shared; the pairing
    choices themselves are specific to this tournament."""
    response = (
        supabase
        .table("tournament_pairs")
        .select(f"*, {_PLAYER_A_EMBED}, {_PLAYER_B_EMBED}")
        .eq("tournament_id", tournament_id)
        .order("created_at")
        .execute()
    )
    return [_flatten_pair(p) for p in (response.data or [])]


def set_tournament_pairs(tournament_id: str, admin_id: str, pairs: list[list[str]]) -> list[dict]:
    """Wholesale replace -- same "resubmit the whole set" convention as
    assign_tee_time_players in tournament_tee_times.py. The admin's pair
    picker always sends its full current list of pairs, so delete-then-
    reinsert keeps this dead simple with no id-matching between old and
    new pairs needed."""
    tournament = _get_tournament(tournament_id)
    if not tournament:
        raise TournamentNotFoundError("Tournament not found.")

    club = _get_club(tournament["club_id"])
    if not club or str(club.get("club_admin")) != str(admin_id):
        raise NotClubAdminError("Only this club's admin can set pairings.")

    eligible_ids = _confirmed_entrant_ids(tournament)

    seen: set[str] = set()
    for pair in pairs:
        if len(pair) != 2:
            raise InvalidPairingError("Each pair needs exactly two players.")
        player_a, player_b = str(pair[0]), str(pair[1])
        if player_a == player_b:
            raise InvalidPairingError("A player can't be paired with themselves.")
        for player_id in (player_a, player_b):
            if player_id not in eligible_ids:
                raise InvalidPairingError("Every paired player must be a confirmed entrant.")
            if player_id in seen:
                raise InvalidPairingError("A player can't be in more than one pair.")
            seen.add(player_id)

    supabase.table("tournament_pairs").delete().eq("tournament_id", tournament_id).execute()

    pair_rows = [
        {"tournament_id": tournament_id, "player_id_a": str(pair[0]), "player_id_b": str(pair[1])}
        for pair in pairs
    ]
    if pair_rows:
        supabase.table("tournament_pairs").insert(pair_rows).execute()

    return list_tournament_pairs(tournament_id)