# target path: backend/routers/player_accounts.py
from fastapi import APIRouter, HTTPException, status

from backend.models.player_account import PlayerAccountCreate, PlayerAccountResponse
from backend.services.player_accounts import (
    DuplicateAccountError,
    create_player_account,
    get_account_by_email,
)


router = APIRouter(
    prefix="/player-accounts",
    tags=["player accounts"],
)


@router.post("/", response_model=PlayerAccountResponse, status_code=status.HTTP_201_CREATED)
def create_player_account_route(account: PlayerAccountCreate):
    try:
        return create_player_account(account)
    except DuplicateAccountError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/email/{email}", response_model=PlayerAccountResponse)
def get_account_by_email_route(email: str):
    account = get_account_by_email(email)

    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found for that email",
        )

    return account