from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PlayerAccountCreate(BaseModel):
    email: str
    name: str
    player_id: UUID


class PlayerAccountResponse(BaseModel):
    id: UUID
    email: str
    name: str
    player_id: UUID
    created_at: datetime | None = None