# target path: backend/services/clubs.py (new file -- replaces backend/services/groups.py, which should be deleted)
import re

from postgrest.exceptions import APIError

from backend.database import supabase
from backend.models.club import ClubCreate
from backend.services.storage import extension_for, upload_image

CLUB_PHOTO_BUCKET = "club-photos"


class DuplicateClubError(Exception):
    """Raised when the generated/provided club code or slug is already taken."""


class ClubNotFoundError(Exception):
    """Raised when club_id doesn't match any club."""


class NotClubAdminError(Exception):
    """Raised if someone other than the club's admin tries to upload its
    photo -- same admin-only gate backend/services/club_invites.py and
    backend/services/tournaments.py already use for their own admin-only
    actions on a club, just not needed here until now since every other
    function in this file is either public reads or club creation itself
    (which doesn't have an admin to check against yet)."""


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


def get_club_by_slug(slug: str) -> dict | None:
    response = (
        supabase
        .table("clubs")
        .select("*")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    return response.data if response is not None else None


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


def get_club(club_id: str) -> dict | None:
    response = (
        supabase
        .table("clubs")
        .select("*")
        .eq("id", club_id)
        .maybe_single()
        .execute()
    )
    return response.data if response is not None else None


def upload_club_photo(
    club_id: str, requesting_player_id: str, file_bytes: bytes, filename: str | None, content_type: str | None
) -> dict | None:
    """Only this club's admin can replace its photo -- unlike a player's
    own profile picture (which is that player's own resource, no admin
    concept involved), a club's photo represents the whole club, shown to
    every member on the home page's clubs grid and the /clubs index, so
    it's gated the same way every other admin-only club action already is
    (see club_invites.send_club_invite / tournaments.create_tournament).
    """
    club = get_club(club_id)
    if not club:
        raise ClubNotFoundError(f"No club with id {club_id}.")

    if str(club.get("club_admin")) != str(requesting_player_id):
        raise NotClubAdminError("Only this club's admin can change its photo.")

    storage_path = f"{club_id}{extension_for(filename)}"
    public_url = upload_image(CLUB_PHOTO_BUCKET, storage_path, file_bytes, content_type)

    response = (
        supabase
        .table("clubs")
        .update({"photo_url": public_url})
        .eq("id", club_id)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]