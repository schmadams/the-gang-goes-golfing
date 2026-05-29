from backend.database import supabase
from backend.models.group_player import GroupPlayerCreate, GroupPlayerDelete


def add_player_to_group(group_player: GroupPlayerCreate) -> dict:
    payload = {
        "group_id": str(group_player.group_id),
        "player_id": str(group_player.player_id),
    }

    response = (
        supabase
        .table("group_players")
        .insert(payload)
        .execute()
    )

    return response.data[0]


def list_players_in_group(group_id: str) -> list[dict]:
    response = (
        supabase
        .table("group_players")
        .select("group_id, player_id, created_at, players(*)")
        .eq("group_id", group_id)
        .order("created_at")
        .execute()
    )

    return response.data


def list_groups_for_player(player_id: str) -> list[dict]:
    response = (
        supabase
        .table("group_players")
        .select("group_id, player_id, created_at, groups(*)")
        .eq("player_id", player_id)
        .order("created_at")
        .execute()
    )

    return response.data


def remove_player_from_group(group_player: GroupPlayerDelete) -> dict | None:
    response = (
        supabase
        .table("group_players")
        .delete()
        .eq("group_id", str(group_player.group_id))
        .eq("player_id", str(group_player.player_id))
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]