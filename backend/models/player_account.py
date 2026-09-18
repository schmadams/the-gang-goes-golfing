from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PlayerAccountCreate(BaseModel):
    email: str
    name: str
    player_id: UUID


class GoogleAccountSignIn(BaseModel):
    """What sign_in_with_google needs from the ID token Google already
    verified for us (see frontend/src/auth_google.py's callback route,
    which does that verification before ever calling this) -- google_id
    is the token's "sub" claim (Google's own stable per-account id, not
    the email, which can change), first_name/surname come from the
    token's given_name/family_name claims so a brand-new account can be
    created without the usual sign-up form asking for them again."""
    email: str
    google_id: str
    first_name: str
    surname: str


class PlayerAccountResponse(BaseModel):
    id: UUID
    email: str
    name: str
    player_id: UUID
    google_id: str | None = None
    created_at: datetime | None = None