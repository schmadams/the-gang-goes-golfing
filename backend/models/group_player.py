from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class GroupPlayerCreate(BaseModel):
    group_id: UUID
    player_id: UUID


class GroupPlayerDelete(BaseModel):
    group_id: UUID
    player_id: UUID


class GroupPlayerResponse(BaseModel):
    group_id: UUID
    player_id: UUID
    created_at: datetime | None = None