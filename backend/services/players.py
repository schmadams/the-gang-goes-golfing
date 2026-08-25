# target path: backend/services/players.py (full replacement)
import os

from backend.database import supabase
from backend.services.friends import are_friends
from backend.services.handicaps import (
    get_current_player_handicap,
    get_handicap_breakdown,
    list_player_handicaps,
)
from backend.services.rounds import get_player_analysis, get_player_scoring_profile, list_player_rounds

PROFILE_PICTURE_BUCKET = "profile-pictures"


def create_player(first_name: str, surname: str, date_of_birth: str | None = None) -> dict:
    payload = {
        "first_name": first_name,
        "surname": surname,
    }

    if date_of_birth:
        payload["date_of_birth"] = date_of_birth

    response = (
        supabase
        .table("players")
        .insert(payload)
        .execute()
    )

    return response.data[0]


def list_players() -> list[dict]:
    response = (
        supabase
        .table("players")
        .select("*")
        .order("created_at")
        .execute()
    )

    return response.data


def get_player(player_id: str) -> dict | None:
    response = (
        supabase
        .table("players")
        .select("*")
        .eq("id", player_id)
        .maybe_single()
        .execute()
    )

    # Some versions of the Supabase client return None outright (rather than
    # a response object with data=None) when maybe_single() finds no match.
    if response is None:
        return None

    return response.data


def update_player(player_id: str, updates: dict) -> dict | None:
    if not updates:
        return get_player(player_id)

    response = (
        supabase
        .table("players")
        .update(updates)
        .eq("id", player_id)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]


def upload_profile_picture(
    player_id: str, file_bytes: bytes, filename: str | None, content_type: str | None
) -> dict | None:
    extension = os.path.splitext(filename or "")[1] or ".jpg"
    storage_path = f"{player_id}{extension}"

    # NOTE: the exact file_options key for "overwrite if it already exists"
    # has changed across supabase-py versions ("upsert" vs "x-upsert"). If
    # this raises an error about an unrecognized option, check what your
    # installed version expects.
    supabase.storage.from_(PROFILE_PICTURE_BUCKET).upload(
        storage_path,
        file_bytes,
        {"content-type": content_type or "image/jpeg", "upsert": "true"},
    )

    public_url = supabase.storage.from_(PROFILE_PICTURE_BUCKET).get_public_url(storage_path)

    return update_player(player_id, {"profile_picture_url": public_url})


class NotFriendsWithPlayerError(Exception):
    """Raised when a player who isn't this player's confirmed friend (and
    isn't the player themselves) tries to load their profile -- see
    get_player_profile's docstring."""


# Fields deliberately left off of get_player_profile's "basic" section --
# date_of_birth, england_golf_number, and phone_number are all still
# stored on the player row (see backend/models/player.py), but none of
# them belong on a page anyone in your friends list can load, unlike the
# name/nickname/home_course/profile_picture_url combination that's
# already effectively public (visible on every scorecard, leaderboard,
# and round summary in the app).
_PROFILE_BASIC_FIELDS = (
    "id",
    "first_name",
    "surname",
    "nickname",
    "home_course",
    "profile_picture_url",
    "created_at",
)


def get_player_profile(player_id: str, viewer_player_id: str) -> dict | None:
    """Aggregates everything the friends-visible player profile page
    needs into one payload: basic (non-sensitive) info, current handicap
    plus its full history and contributing-rounds breakdown (the same
    data the home page's own Handicap panel uses), the last 5 rounds, and
    the Player Analysis putts/fairway trend points -- all for `player_id`,
    all in one request rather than the 5+ separate round trips the home
    page and Analysis page each make for the *signed-in* player's own
    data.

    Returns None if player_id doesn't exist (the router turns that into a
    404). Raises NotFriendsWithPlayerError if viewer_player_id is neither
    player_id itself nor a confirmed friend of player_id (the router turns
    that into a 403) -- enforced here, not just by hiding the link in the
    UI, since this is the one place all of a player's round history and
    handicap data is assembled together and anyone could otherwise hit
    this endpoint directly with any player_id they happened to know."""
    player = get_player(player_id)
    if not player:
        return None

    if viewer_player_id != player_id and not are_friends(player_id, viewer_player_id):
        raise NotFriendsWithPlayerError(
            "You can only view the profile of a confirmed friend."
        )

    basic = {field: player.get(field) for field in _PROFILE_BASIC_FIELDS}

    current_handicap_row = get_current_player_handicap(player_id)

    return {
        "player": basic,
        "current_handicap": current_handicap_row["handicap"] if current_handicap_row else None,
        "handicap_history": list_player_handicaps(player_id),
        "handicap_breakdown": get_handicap_breakdown(player_id),
        # Same list_player_rounds an in-progress/pending_signoff round
        # rides ahead of, unlimited, exactly like the home page's own
        # Rounds History panel -- limit=5 only caps the *completed*
        # bucket, so "last 5" here means "last 5 completed, plus
        # whatever's actively being played right now", which is the more
        # useful read of "recent rounds" on a friend's profile anyway.
        "recent_rounds": list_player_rounds(player_id, limit=5),
        "analysis_points": get_player_analysis(player_id),
        "scoring_profile": get_player_scoring_profile(player_id),
    }


def delete_player(player_id: str) -> dict | None:
    response = (
        supabase
        .table("players")
        .delete()
        .eq("id", player_id)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]