from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class GroupCreate(BaseModel):
    code: str
    slug: str
    name: str
    description: str | None = None


class GroupResponse(BaseModel):
    id: UUID
    code: str
    slug: str
    name: str
    description: str | None = None
    created_at: datetime | None = None