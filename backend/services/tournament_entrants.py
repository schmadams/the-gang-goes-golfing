# target path: backend/services/tournament_entrants.py (new file)
from datetime import datetime, timezone

from backend.database import supabase
from backend.services.handicaps import get_current_player_handicap, get_effective_handicap_source

_PLAYER_EMBED = "players(id, first_name, surname, nickname)"


class TournamentNotFoundError(Exception):
    """Raised when tournament_id doesn't match any tournament."""


class NotClubAdminError(Exception):
    """Raised if someone other than the tournament's club admin tries to
    approve/reject an application."""


class AlreadyEnteredError(Exception):
    """Raised if the player already has a pending or confirmed entry for
    this tournament -- a past withdrawal/rejection doesn't block
    re-entering (see enter_tournament)."""


class HandicapOutOfRangeError(Exception):
    """Raised when a self-entry's handicap falls outside the tournament's
    min/max range. Self-entry has no admin in the loop to catch this at
    approval time, so it's enforced immediately instead -- an
    entry_mode='approval' tournament never raises this; being outside
    range there is just something the admin sees when deciding, not an
    automatic block (see docstring on enter_tournament)."""


def _get_tournament(tournament_id: str) -> dict | None:
    response = supabase.table("tournaments").select("*").eq("id", tournament_id).maybe_single().execute()
    return response.data if response is not None else None


def _get_club(club_id: str) -> dict | None:
    response = supabase.table("clubs").select("*").eq("id", club_id).maybe_single().execute()
    return response.data if response is not None else None


def _flatten_player(entrant: dict) -> dict:
    player = entrant.pop("players", None) or {}
    return {
        **entrant,
        "first_name": player.get("first_name"),
        "surname": player.get("surname"),
        "nickname": player.get("nickname"),
    }


def _in_range(handicap, min_handicap, max_handicap) -> bool:
    if handicap is None:
        # No handicap on file yet -- nothing to compare against, so this
        # doesn't block. Whatever handicap they end up with is captured at
        # entry time anyway (handicap_at_entry), visible to an approving
        # admin either way.
        return True
    if min_handicap is not None and handicap < min_handicap:
        return False
    if max_handicap is not None and handicap > max_handicap:
        return False
    return True


def list_entrants_for_tournament(tournament_id: str) -> list[dict]:
    response = (
        supabase
        .table("tournament_entrants")
        .select(f"*, {_PLAYER_EMBED}")
        .eq("tournament_id", tournament_id)
        .order("created_at")
        .execute()
    )
    return [_flatten_player(e) for e in (response.data or [])]


def enter_tournament(tournament_id: str, player_id: str, handicap_source: str | None = None) -> dict:
    """
    entry_mode='self': confirmed immediately, unless a min/max handicap is
    set on the tournament and the player's current handicap falls outside
    it -- there's no admin step in self-entry to catch that, so it's
    rejected outright here instead.

    entry_mode='approval': always goes to 'pending' regardless of
    handicap -- being outside range is information the admin sees on the
    application (handicap_at_entry alongside the tournament's own min/max),
    not an automatic rejection. Approve/reject is their call.
    """
    tournament = _get_tournament(tournament_id)
    if not tournament:
        raise TournamentNotFoundError("Tournament not found.")

    existing_response = (
        supabase
        .table("tournament_entrants")
        .select("*")
        .eq("tournament_id", tournament_id)
        .eq("player_id", player_id)
        .maybe_single()
        .execute()
    )
    existing = existing_response.data if existing_response is not None else None
    if existing and existing["status"] in ("pending", "confirmed"):
        raise AlreadyEnteredError("You've already entered this tournament.")

    resolved_source = get_effective_handicap_source(player_id, handicap_source)
    handicap_row = get_current_player_handicap(player_id, source=resolved_source)
    handicap = handicap_row["handicap"] if handicap_row else None

    if tournament["entry_mode"] == "self":
        if not _in_range(handicap, tournament.get("min_handicap"), tournament.get("max_handicap")):
            lo = tournament.get("min_handicap")
            hi = tournament.get("max_handicap")
            raise HandicapOutOfRangeError(
                f"This tournament is open to handicaps between {lo if lo is not None else '-'} "
                f"and {hi if hi is not None else '-'}."
            )
        status = "confirmed"
    else:
        status = "pending"

    payload = {
        "tournament_id": tournament_id,
        "player_id": player_id,
        "status": status,
        "handicap_at_entry": handicap,
        "handicap_source": resolved_source,
    }

    if existing:
        # A previous withdrawal/rejection left a row behind -- update it in
        # place so re-entering doesn't hit the one-entry-per-player unique
        # constraint with a fresh insert.
        response = (
            supabase
            .table("tournament_entrants")
            .update(payload)
            .eq("id", existing["id"])
            .execute()
        )
    else:
        response = supabase.table("tournament_entrants").insert(payload).execute()

    return response.data[0]


def withdraw_entrant(tournament_id: str, player_id: str) -> dict | None:
    response = (
        supabase
        .table("tournament_entrants")
        .update({"status": "withdrawn"})
        .eq("tournament_id", tournament_id)
        .eq("player_id", player_id)
        .execute()
    )
    return response.data[0] if response.data else None


def _respond_to_entrant(tournament_id: str, player_id: str, admin_id: str, new_status: str) -> dict | None:
    tournament = _get_tournament(tournament_id)
    if not tournament:
        raise TournamentNotFoundError("Tournament not found.")

    club = _get_club(tournament["club_id"])
    if not club or str(club.get("club_admin")) != admin_id:
        raise NotClubAdminError("Only this club's admin can respond to tournament applications.")

    response = (
        supabase
        .table("tournament_entrants")
        .update({"status": new_status, "responded_at": datetime.now(timezone.utc).isoformat()})
        .eq("tournament_id", tournament_id)
        .eq("player_id", player_id)
        .execute()
    )
    return response.data[0] if response.data else None


def approve_entrant(tournament_id: str, player_id: str, admin_id: str) -> dict | None:
    return _respond_to_entrant(tournament_id, player_id, admin_id, "confirmed")


def reject_entrant(tournament_id: str, player_id: str, admin_id: str) -> dict | None:
    return _respond_to_entrant(tournament_id, player_id, admin_id, "rejected")


def admin_add_entrant(tournament_id: str, player_id: str, admin_id: str) -> dict:
    """Admin adds a player directly as a confirmed entrant -- bypasses
    entry_mode/handicap checks entirely, since those exist to gate
    self-service applications, not an admin's own judgement call about who
    should be in their tournament."""
    tournament = _get_tournament(tournament_id)
    if not tournament:
        raise TournamentNotFoundError("Tournament not found.")

    club = _get_club(tournament["club_id"])
    if not club or str(club.get("club_admin")) != admin_id:
        raise NotClubAdminError("Only this club's admin can add entrants.")

    existing_response = (
        supabase
        .table("tournament_entrants")
        .select("*")
        .eq("tournament_id", tournament_id)
        .eq("player_id", player_id)
        .maybe_single()
        .execute()
    )
    existing = existing_response.data if existing_response is not None else None
    if existing and existing["status"] in ("pending", "confirmed"):
        raise AlreadyEnteredError("Player has already entered this tournament.")

    resolved_source = get_effective_handicap_source(player_id)
    handicap_row = get_current_player_handicap(player_id, source=resolved_source)
    handicap = handicap_row["handicap"] if handicap_row else None

    payload = {
        "tournament_id": tournament_id,
        "player_id": player_id,
        "status": "confirmed",
        "handicap_at_entry": handicap,
        "handicap_source": resolved_source,
        "responded_at": datetime.now(timezone.utc).isoformat(),
    }

    if existing:
        # A previous withdrawal/rejection left a row behind -- update it in
        # place, same as enter_tournament does, so this doesn't hit the
        # one-entry-per-player unique constraint with a fresh insert.
        response = (
            supabase
            .table("tournament_entrants")
            .update(payload)
            .eq("id", existing["id"])
            .execute()
        )
    else:
        response = supabase.table("tournament_entrants").insert(payload).execute()

    return response.data[0]


def admin_remove_entrant(tournament_id: str, player_id: str, admin_id: str) -> dict | None:
    """Admin-initiated removal of any entrant (pending or confirmed) --
    unlike withdraw_entrant (which the player calls on themselves with no
    separate auth check), this targets an arbitrary player_id, so it needs
    its own admin check before reusing the same withdraw semantics."""
    tournament = _get_tournament(tournament_id)
    if not tournament:
        raise TournamentNotFoundError("Tournament not found.")

    club = _get_club(tournament["club_id"])
    if not club or str(club.get("club_admin")) != admin_id:
        raise NotClubAdminError("Only this club's admin can remove entrants.")

    return withdraw_entrant(tournament_id, player_id)