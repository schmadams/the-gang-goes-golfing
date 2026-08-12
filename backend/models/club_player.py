# target path: backend/models/club_player.py (new file -- replaces backend/models/group_player.py, which should be deleted)
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ClubPlayerCreate(BaseModel):
    club_id: UUID
    player_id: UUID


class ClubPlayerDelete(BaseModel):
    club_id: UUID
    player_id: UUID


class ClubPlayerResponse(BaseModel):
    club_id: UUID
    player_id: UUID
    created_at: datetime | None = None