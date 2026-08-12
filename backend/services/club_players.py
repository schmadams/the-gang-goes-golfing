# target path: backend/services/club_players.py (new file -- replaces backend/services/group_players.py, which should be deleted)
from backend.database import supabase
from backend.models.club_player import ClubPlayerCreate, ClubPlayerDelete


def add_player_to_club(club_player: ClubPlayerCreate) -> dict:
    payload = {
        "club_id": str(club_player.club_id),
        "player_id": str(club_player.player_id),
    }

    response = (
        supabase
        .table("club_players")
        .insert(payload)
        .execute()
    )

    return response.data[0]


def list_players_in_club(club_id: str) -> list[dict]:
    response = (
        supabase
        .table("club_players")
        .select("club_id, player_id, created_at, players(*)")
        .eq("club_id", club_id)
        .order("created_at")
        .execute()
    )

    return response.data


def list_clubs_for_player(player_id: str) -> list[dict]:
    response = (
        supabase
        .table("club_players")
        .select("club_id, player_id, created_at, clubs(*)")
        .eq("player_id", player_id)
        .order("created_at")
        .execute()
    )

    return response.data


def remove_player_from_club(club_player: ClubPlayerDelete) -> dict | None:
    response = (
        supabase
        .table("club_players")
        .delete()
        .eq("club_id", str(club_player.club_id))
        .eq("player_id", str(club_player.player_id))
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]