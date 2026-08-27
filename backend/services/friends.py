# target path: backend/services/friends.py (new file)
from datetime import datetime, timezone

from backend.database import supabase
from backend.services.club_players import list_clubs_for_player, list_players_in_club
from backend.services.notifications import create_notification

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


class NotRequestSenderError(Exception):
    """Raised if a player tries to cancel a request they didn't send --
    the recipient already has Accept/Decline for that."""


class PlayerNotFoundError(Exception):
    """Raised when recipient_id doesn't match any player. Worth
    distinguishing from a generic DB error now that requests are sent by
    typing in a Player ID directly rather than picking from a list --
    a typo'd ID should say so clearly instead of failing some other way."""


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

    recipient = _get_player(recipient_id)
    if not recipient:
        raise PlayerNotFoundError("No player found with that ID.")

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

    # Best-effort, same "never let a notification failing block the real
    # action" convention as every other create_notification call site --
    # the request itself is already inserted above by this point.
    try:
        requester = _get_player(requester_id)
        requester_name = (
            (requester or {}).get("nickname")
            or f"{(requester or {}).get('first_name', '')} {(requester or {}).get('surname', '')}".strip()
            or "Someone"
        )
        create_notification(
            recipient_id,
            "friends",
            f"{requester_name} sent you a friend request",
            url="/friends",
        )
    except Exception as exc:
        print(f"[NOTIFY] Failed to notify {recipient_id} of friend request from {requester_id}: {exc}")

    # Attaches the recipient's name to the response -- requests are now
    # sent by typing a Player ID rather than picking a name off a list, so
    # without this the confirmation modal that follows would have no name
    # to show, only the ID that was just typed in.
    return {**response.data[0], "recipient": recipient}


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


def list_clubmates_available_to_add(player_id: str) -> list[dict]:
    """Every player who shares at least one club with this player, minus
    anyone there's no point offering: this player themself, anyone
    already a confirmed friend, and anyone with a friend request already
    pending between the two of them in either direction (an already-
    declined request doesn't block this -- same "declined isn't final"
    rule _existing_request already applies to sending a fresh request by
    ID). Powers the "Add from your Clubs" panel on the Friends page (see
    frontend/src/pages/friends.py) -- the alternative to typing in a
    Player ID by hand, for the common case of wanting to friend someone
    you already know through a shared club.

    Returns [{"player_id", "first_name", "surname", "nickname",
    "club_names"}, ...], sorted by name. club_names is every club shared
    with this player (usually just one, but a pair of players in more
    than one club together will have all of them listed) -- shown
    alongside their name so it's clear where you know them from."""
    my_clubs = list_clubs_for_player(player_id)
    club_name_by_id = {
        row["club_id"]: (row.get("clubs") or {}).get("name", "a club")
        for row in my_clubs
    }
    if not club_name_by_id:
        return []

    clubmates: dict[str, dict] = {}
    for club_id, club_name in club_name_by_id.items():
        for row in list_players_in_club(club_id):
            other_id = row.get("player_id")
            if not other_id or other_id == player_id:
                continue
            other = row.get("players") or {}
            entry = clubmates.setdefault(other_id, {
                "player_id": other_id,
                "first_name": other.get("first_name"),
                "surname": other.get("surname"),
                "nickname": other.get("nickname"),
                "club_names": [],
            })
            if club_name not in entry["club_names"]:
                entry["club_names"].append(club_name)

    if not clubmates:
        return []

    # Exclude confirmed friends and anyone with a pending request already
    # between us in either direction -- same "nothing worth offering
    # twice" idea as play.py's upload-round-friend-picker filtering
    # already-selected friends back out of its own remaining options,
    # just against club rosters instead of an in-progress selection.
    existing_response = (
        supabase
        .table("friend_requests")
        .select("requester_id, recipient_id, status")
        .or_(f"requester_id.eq.{player_id},recipient_id.eq.{player_id}")
        .neq("status", "declined")
        .execute()
    )
    for row in (existing_response.data or []):
        other_id = row["recipient_id"] if row["requester_id"] == player_id else row["requester_id"]
        clubmates.pop(other_id, None)

    return sorted(
        clubmates.values(),
        key=lambda p: p.get("nickname") or f"{p.get('first_name', '')} {p.get('surname', '')}".strip(),
    )


def are_friends(player_a: str, player_b: str) -> bool:
    """True if these two players have a confirmed (accepted) friendship in
    either direction. Used to gate anything that shouldn't be visible to
    just anyone with a player_id -- currently the player profile page (see
    get_player_profile in backend/services/players.py) -- the same way
    round membership already gates round data elsewhere in this app."""
    response = (
        supabase
        .table("friend_requests")
        .select("id")
        .or_(
            f"and(requester_id.eq.{player_a},recipient_id.eq.{player_b}),"
            f"and(requester_id.eq.{player_b},recipient_id.eq.{player_a})"
        )
        .eq("status", "accepted")
        .execute()
    )
    return bool(response.data)


def remove_friend(player_id: str, friend_id: str) -> bool:
    """Removes a confirmed friendship in either direction by deleting the
    underlying accepted friend_requests row. Returns False if these two
    players aren't currently friends. Deleting (rather than e.g. setting a
    'removed' status, which the DB check constraint doesn't even allow)
    mirrors cancel_friend_request -- it also means either player is free
    to send a fresh request later without a stale row blocking it, since
    _existing_request only guards against pending/accepted rows."""
    response = (
        supabase
        .table("friend_requests")
        .select("*")
        .or_(
            f"and(requester_id.eq.{player_id},recipient_id.eq.{friend_id}),"
            f"and(requester_id.eq.{friend_id},recipient_id.eq.{player_id})"
        )
        .eq("status", "accepted")
        .execute()
    )
    rows = response.data or []
    if not rows:
        return False

    delete_response = supabase.table("friend_requests").delete().eq("id", rows[0]["id"]).execute()
    return bool(delete_response.data)


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


def cancel_friend_request(request_id: str, player_id: str) -> bool:
    """Deletes a still-pending outgoing request -- only the player who
    sent it can cancel it. Returns False (rather than raising) if the
    request is already gone or has already been responded to, since
    either way there's nothing left to cancel."""
    response = supabase.table("friend_requests").select("*").eq("id", request_id).maybe_single().execute()
    row = response.data if response is not None else None

    if not row:
        return False
    if row["requester_id"] != player_id:
        raise NotRequestSenderError("Only the player who sent this request can cancel it.")
    if row["status"] != "pending":
        return False

    delete_response = supabase.table("friend_requests").delete().eq("id", request_id).execute()
    return bool(delete_response.data)