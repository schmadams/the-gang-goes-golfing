# target path: backend/services/groups.py (full replacement)
import re

from postgrest.exceptions import APIError

from backend.database import supabase
from backend.models.group import GroupCreate


class DuplicateGroupError(Exception):
    """Raised when the generated/provided group code or slug is already taken."""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return slug or "group"


def list_groups() -> list[dict]:
    response = (
        supabase
        .table("groups")
        .select("*")
        .order("created_at")
        .execute()
    )

    return response.data


def create_group(group: GroupCreate) -> dict:
    slug = group.slug or _slugify(group.name)
    code = group.code or slug

    payload = {
        "code": code,
        "slug": slug,
        "name": group.name,
        "description": group.description,
        "group_admin": str(group.admin_player_id) if group.admin_player_id else None,
    }

    try:
        response = (
            supabase
            .table("groups")
            .insert(payload)
            .execute()
        )
    except APIError as exc:
        if exc.code == "23505":
            raise DuplicateGroupError(
                "A group with a similar name already exists. Try a different name."
            ) from exc
        raise

    return response.data[0]


def delete_group(group_id: str) -> dict | None:
    response = (
        supabase
        .table("groups")
        .delete()
        .eq("id", group_id)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]