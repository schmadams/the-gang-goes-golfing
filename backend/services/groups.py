from backend.database import supabase
from backend.models.group import GroupCreate


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
    payload = group.model_dump()

    response = (
        supabase
        .table("groups")
        .insert(payload)
        .execute()
    )

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