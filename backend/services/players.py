# target path: backend/services/players.py (full replacement)
import os

from backend.database import supabase

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