# target path: backend/models/friend.py (new file)
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class FriendRequestCreate(BaseModel):
    requester_id: UUID
    recipient_id: UUID


class FriendResponse(BaseModel):
    """A confirmed friend, from the perspective of the player who asked --
    just the other player's identity, not the underlying request row."""
    player_id: UUID
    first_name: Optional[str] = None
    surname: Optional[str] = None
    nickname: Optional[str] = None