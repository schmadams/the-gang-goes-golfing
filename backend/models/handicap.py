from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class HandicapCreate(BaseModel):
    player_id: UUID
    handicap: float
    valid_from: date | None = None


class HandicapResponse(BaseModel):
    id: UUID
    player_id: UUID
    handicap: float
    valid_from: date
    created_at: datetime | None = None