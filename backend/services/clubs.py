# target path: backend/services/clubs.py (new file -- replaces backend/services/groups.py, which should be deleted)
import re

from postgrest.exceptions import APIError

from backend.database import supabase
from backend.models.club import ClubCreate


class DuplicateClubError(Exception):
    """Raised when the generated/provided club code or slug is already taken."""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return slug or "club"


def list_clubs() -> list[dict]:
    response = (
        supabase
        .table("clubs")
        .select("*")
        .order("created_at")
        .execute()
    )

    return response.data


def create_club(club: ClubCreate) -> dict:
    slug = club.slug or _slugify(club.name)
    code = club.code or slug

    payload = {
        "code": code,
        "slug": slug,
        "name": club.name,
        "description": club.description,
        "club_admin": str(club.admin_player_id) if club.admin_player_id else None,
    }

    try:
        response = (
            supabase
            .table("clubs")
            .insert(payload)
            .execute()
        )
    except APIError as exc:
        if exc.code == "23505":
            raise DuplicateClubError(
                "A club with a similar name already exists. Try a different name."
            ) from exc
        raise

    return response.data[0]


def delete_club(club_id: str) -> dict | None:
    response = (
        supabase
        .table("clubs")
        .delete()
        .eq("id", club_id)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]