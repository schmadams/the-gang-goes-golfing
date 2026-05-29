from backend.database import supabase


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

    return response.data


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