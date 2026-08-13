# target path: backend/services/club_invites.py (new file)
from datetime import datetime, timezone

from backend.database import supabase
from backend.models.club_player import ClubPlayerCreate
from backend.services.club_players import add_player_to_club

# Same PostgREST embed-hint pattern as backend/services/friends.py --
# club_invites has two FKs onto players (inviter_id, invitee_id), so
# select("*, players(...)") would be ambiguous without naming which one.
_INVITEE_EMBED = "invitee:players!club_invites_invitee_id_fkey(id,first_name,surname,nickname)"
_INVITER_EMBED = "inviter:players!club_invites_inviter_id_fkey(id,first_name,surname,nickname)"


class ClubNotFoundError(Exception):
    """Raised when club_id doesn't match any club."""


class NotClubAdminError(Exception):
    """Raised if someone other than the club's admin tries to send an
    invite -- invites are the only way into a club now (see
    club_players.py), so this is what actually enforces that."""


class PlayerNotFoundError(Exception):
    """Raised when invitee_id doesn't match any player."""


class AlreadyMemberError(Exception):
    """Raised if the invitee is already in the club."""


class AlreadyInvitedError(Exception):
    """Raised if there's already a pending invite for this player to
    this club."""


class NotInviteRecipientError(Exception):
    """Raised if a player tries to accept/decline an invite that wasn't
    sent to them."""


class NotInviteSenderError(Exception):
    """Raised if someone other than the inviting admin tries to cancel
    an invite."""


def _get_club(club_id: str) -> dict | None:
    response = supabase.table("clubs").select("*").eq("id", club_id).maybe_single().execute()
    return response.data if response is not None else None


def _get_player(player_id: str) -> dict | None:
    response = (
        supabase
        .table("players")
        .select("id, first_name, surname, nickname")
        .eq("id", player_id)
        .maybe_single()
        .execute()
    )
    return response.data if response is not None else None


def send_club_invite(club_id: str, inviter_id: str, invitee_id: str) -> dict:
    club = _get_club(club_id)
    if not club:
        raise ClubNotFoundError("Club not found.")
    if str(club.get("club_admin")) != inviter_id:
        raise NotClubAdminError("Only this club's admin can invite players.")

    invitee = _get_player(invitee_id)
    if not invitee:
        raise PlayerNotFoundError("No player found with that ID.")

    existing_member = (
        supabase
        .table("club_players")
        .select("club_id")
        .eq("club_id", club_id)
        .eq("player_id", invitee_id)
        .maybe_single()
        .execute()
    )
    if existing_member is not None and existing_member.data:
        raise AlreadyMemberError("That player is already in this club.")

    existing_invite = (
        supabase
        .table("club_invites")
        .select("id")
        .eq("club_id", club_id)
        .eq("invitee_id", invitee_id)
        .eq("status", "pending")
        .maybe_single()
        .execute()
    )
    if existing_invite is not None and existing_invite.data:
        raise AlreadyInvitedError("There's already a pending invite for that player.")

    response = (
        supabase
        .table("club_invites")
        .insert({"club_id": club_id, "inviter_id": inviter_id, "invitee_id": invitee_id})
        .execute()
    )
    # Attaches the invitee's name -- same reason friends.send_friend_request
    # does this: the confirmation that follows needs a name to show, not
    # just the ID that was just typed in.
    return {**response.data[0], "invitee": invitee}


def list_pending_invites_for_player(player_id: str) -> list[dict]:
    response = (
        supabase
        .table("club_invites")
        .select(f"*, clubs(name, slug), {_INVITER_EMBED}")
        .eq("invitee_id", player_id)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def list_pending_invites_for_club(club_id: str) -> list[dict]:
    response = (
        supabase
        .table("club_invites")
        .select(f"*, {_INVITEE_EMBED}")
        .eq("club_id", club_id)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def respond_to_club_invite(invite_id: str, player_id: str, accept: bool) -> dict | None:
    """Only the invitee can accept/decline. Accepting both updates the
    invite and adds the club_players row in the same call -- calling
    add_player_to_club directly (a plain Python call, not another HTTP
    round trip) rather than going back through POST /club-players/,
    which stays a generic, unauthenticated endpoint elsewhere in the app
    (used e.g. to auto-join a club's creator on create_club) -- this is
    what actually makes "accept an invite" the only path into a club
    through the UI now that Join Club by UUID is gone."""
    response = supabase.table("club_invites").select("*").eq("id", invite_id).maybe_single().execute()
    row = response.data if response is not None else None

    if not row:
        return None
    if row["invitee_id"] != player_id:
        raise NotInviteRecipientError("Only the invited player can respond to this invite.")

    update_response = (
        supabase
        .table("club_invites")
        .update({
            "status": "accepted" if accept else "declined",
            "responded_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", invite_id)
        .execute()
    )
    updated = update_response.data[0] if update_response.data else None

    if accept and updated:
        add_player_to_club(ClubPlayerCreate(club_id=row["club_id"], player_id=player_id))

    return updated


def cancel_club_invite(invite_id: str, admin_id: str) -> bool:
    """Deletes a still-pending invite -- only the admin who sent it can
    cancel it. Returns False (rather than raising) if it's already gone
    or already responded to, since either way there's nothing left to
    cancel -- same pattern as friends.cancel_friend_request."""
    response = supabase.table("club_invites").select("*").eq("id", invite_id).maybe_single().execute()
    row = response.data if response is not None else None

    if not row:
        return False
    if row["inviter_id"] != admin_id:
        raise NotInviteSenderError("Only the admin who sent this invite can cancel it.")
    if row["status"] != "pending":
        return False

    delete_response = supabase.table("club_invites").delete().eq("id", invite_id).execute()
    return bool(delete_response.data)   