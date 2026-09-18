# target path: backend/routers/player_accounts.py
from fastapi import APIRouter, HTTPException, status

from backend.models.player_account import GoogleAccountSignIn, PlayerAccountCreate, PlayerAccountResponse
from backend.services.player_accounts import (
    DuplicateAccountError,
    create_player_account,
    get_account_by_email,
    sign_in_with_google,
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


@router.post("/google", response_model=PlayerAccountResponse)
def sign_in_with_google_route(payload: GoogleAccountSignIn):
    # No error branch needed here the way create_player_account_route has
    # one -- sign_in_with_google already resolves every collision itself
    # (that's the whole point of it: find-by-google_id, then
    # find-by-email-and-link, then create, with a race-condition retry on
    # top of that last step) rather than ever expecting the caller to
    # react to a 409. See that function's own docstring for the three
    # cases. frontend/src/auth_google.py's callback route is the only
    # caller -- only reachable after it's independently verified the
    # Google ID token this payload was built from.
    return sign_in_with_google(payload)