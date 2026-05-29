from backend.database import supabase
from backend.models.handicap import HandicapCreate


def add_player_handicap(handicap: HandicapCreate) -> dict:
    payload = {
        "player_id": str(handicap.player_id),
        "handicap": handicap.handicap,
    }

    if handicap.valid_from:
        payload["valid_from"] = handicap.valid_from.isoformat()

    response = (
        supabase
        .table("player_handicaps")
        .insert(payload)
        .execute()
    )

    return response.data[0]


def list_player_handicaps(player_id: str) -> list[dict]:
    response = (
        supabase
        .table("player_handicaps")
        .select("*")
        .eq("player_id", player_id)
        .order("valid_from", desc=True)
        .execute()
    )

    return response.data


def get_current_player_handicap(player_id: str) -> dict | None:
    response = (
        supabase
        .table("player_handicaps")
        .select("*")
        .eq("player_id", player_id)
        .order("valid_from", desc=True)
        .limit(1)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]


def list_latest_handicaps_for_group(group_id: str) -> list[dict]:
    group_players_response = (
        supabase
        .table("group_players")
        .select("group_id, player_id, players(id, first_name, surname, date_of_birth)")
        .eq("group_id", group_id)
        .execute()
    )

    group_player_rows = group_players_response.data

    results = []

    for row in group_player_rows:
        player = row.get("players") or {}
        player_id = row["player_id"]

        handicap_response = (
            supabase
            .table("player_handicaps")
            .select("id, player_id, handicap, valid_from, created_at")
            .eq("player_id", player_id)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )

        latest_handicap = handicap_response.data[0] if handicap_response.data else None

        results.append(
            {
                "group_id": row["group_id"],
                "player_id": player_id,
                "first_name": player.get("first_name"),
                "surname": player.get("surname"),
                "date_of_birth": player.get("date_of_birth"),
                "latest_handicap": latest_handicap,
            }
        )

    return results