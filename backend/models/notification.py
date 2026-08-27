# target path: backend/models/notification.py (new file)
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: UUID
    player_id: UUID
    category: str
    title: str
    body: Optional[str] = None
    url: Optional[str] = None
    created_at: datetime
    read_at: Optional[datetime] = None