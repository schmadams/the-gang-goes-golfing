# target path: backend/services/player_accounts.py
from postgrest.exceptions import APIError

from backend.database import supabase
from backend.models.player_account import PlayerAccountCreate


class DuplicateAccountError(Exception):
    """Raised when the email or player_id is already linked to an account."""


def create_player_account(account: PlayerAccountCreate) -> dict:
    payload = {
        "email": account.email,
        "name": account.name,
        "player_id": str(account.player_id),
    }

    try:
        response = (
            supabase
            .table("player_accounts")
            .insert(payload)
            .execute()
        )
    except APIError as exc:
        # Postgres unique-violation error code
        if exc.code == "23505":
            if "player_accounts_player_id_unique" in (exc.message or ""):
                raise DuplicateAccountError(
                    f"Player {account.player_id} already has an account."
                ) from exc
            if "email" in (exc.message or ""):
                raise DuplicateAccountError(
                    f"An account already exists for {account.email}."
                ) from exc
            raise DuplicateAccountError("This account already exists.") from exc
        raise

    return response.data[0]


def get_account_by_email(email: str) -> dict | None:
    response = (
        supabase
        .table("player_accounts")
        .select("*")
        .eq("email", email)
        .maybe_single()
        .execute()
    )

    return response.data