# target path: backend/models/group.py (full replacement)
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    admin_player_id: UUID | None = None  # frontend always sends this; CLI usage can omit it
    code: str | None = None  # auto-generated from name if not provided
    slug: str | None = None  # auto-generated from name if not provided


class GroupResponse(BaseModel):
    id: UUID
    code: str
    slug: str
    name: str
    description: str | None = None
    group_admin: UUID | None = None
    created_at: datetime | None = None