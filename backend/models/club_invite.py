# target path: backend/models/club_invite.py (new file)
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ClubInviteCreate(BaseModel):
    club_id: UUID
    inviter_id: UUID
    invitee_id: UUID


class ClubInviteResponse(BaseModel):
    id: UUID
    club_id: UUID
    inviter_id: UUID
    invitee_id: UUID
    status: str
    created_at: datetime
    responded_at: Optional[datetime] = None