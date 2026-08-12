# target path: backend/services/friends.py (new file)
from datetime import datetime, timezone

from backend.database import supabase

# PostgREST embed hints for the two FKs friend_requests has onto players --
# without the "!constraint_name" hint, a select("*, players(...)") would be
# ambiguous (which FK does it mean?) since there are two. These follow
# Postgres's default auto-generated constraint naming ("{table}_{column}_fkey"),
# which is what friend_requests_migration.sql relies on rather than naming
# the constraints explicitly.
_REQUESTER_EMBED = "requester:players!friend_requests_requester_id_fkey(id,first_name,surname,nickname)"
_RECIPIENT_EMBED = "recipient:players!friend_requests_recipient_id_fkey(id,first_name,surname,nickname)"


class SelfFriendRequestError(Exception):
    """Raised if a player tries to friend-request themselves."""


class AlreadyFriendsOrPendingError(Exception):
    """Raised when a friend request already exists between these two
    players (pending or accepted) in either direction."""


class NotRequestRecipientError(Exception):
    """Raised if a player tries to accept/decline a request that wasn't
    sent to them."""


def _existing_request(player_a: str, player_b: str) -> dict | None:
    response = (
        supabase
        .table("friend_requests")
        .select("*")
        .or_(
            f"and(requester_id.eq.{player_a},recipient_id.eq.{player_b}),"
            f"and(requester_id.eq.{player_b},recipient_id.eq.{player_a})"
        )
        .execute()
    )
    live_rows = [r for r in (response.data or []) if r["status"] != "declined"]
    return live_rows[0] if live_rows else None


def send_friend_request(requester_id: str, recipient_id: str) -> dict:
    if requester_id == recipient_id:
        raise SelfFriendRequestError("You can't send a friend request to yourself.")

    if _existing_request(requester_id, recipient_id):
        raise AlreadyFriendsOrPendingError(
            "You're already friends, or there's already a pending request between you."
        )

    response = (
        supabase
        .table("friend_requests")
        .insert({"requester_id": requester_id, "recipient_id": recipient_id})
        .execute()
    )
    return response.data[0]


def list_pending_requests(player_id: str) -> dict:
    """Returns {'incoming': [...], 'outgoing': [...]} -- pending requests
    either sent to or sent by this player, each with the other player's
    name attached so the frontend doesn't need a second lookup."""
    response = (
        supabase
        .table("friend_requests")
        .select(f"*, {_REQUESTER_EMBED}, {_RECIPIENT_EMBED}")
        .or_(f"requester_id.eq.{player_id},recipient_id.eq.{player_id}")
        .eq("status", "pending")
        .order("created_at", desc=True)
        .execute()
    )
    rows = response.data or []
    return {
        "incoming": [r for r in rows if r["recipient_id"] == player_id],
        "outgoing": [r for r in rows if r["requester_id"] == player_id],
    }


def list_friends(player_id: str) -> list[dict]:
    """Confirmed friends (accepted requests in either direction), flattened
    to just the *other* player in each row."""
    response = (
        supabase
        .table("friend_requests")
        .select(f"*, {_REQUESTER_EMBED}, {_RECIPIENT_EMBED}")
        .or_(f"requester_id.eq.{player_id},recipient_id.eq.{player_id}")
        .eq("status", "accepted")
        .execute()
    )
    rows = response.data or []

    friends = []
    for row in rows:
        other = row["recipient"] if row["requester_id"] == player_id else row["requester"]
        if other:
            friends.append({
                "player_id": other["id"],
                "first_name": other.get("first_name"),
                "surname": other.get("surname"),
                "nickname": other.get("nickname"),
            })
    return friends


def respond_to_friend_request(request_id: str, player_id: str, accept: bool) -> dict | None:
    """Only the recipient can accept/decline -- the requester waits."""
    response = supabase.table("friend_requests").select("*").eq("id", request_id).maybe_single().execute()
    row = response.data if response is not None else None

    if not row:
        return None
    if row["recipient_id"] != player_id:
        raise NotRequestRecipientError("Only the player who received this request can respond to it.")

    update_response = (
        supabase
        .table("friend_requests")
        .update({
            "status": "accepted" if accept else "declined",
            "responded_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", request_id)
        .execute()
    )
    return update_response.data[0] if update_response.data else None