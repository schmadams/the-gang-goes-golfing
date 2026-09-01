# target path: backend/models/player.py (full replacement)
from datetime import date
from typing import Optional

from pydantic import BaseModel


class PlayerCreate(BaseModel):
    first_name: str
    surname: str
    date_of_birth: Optional[date] = None


class PlayerUpdate(BaseModel):
    first_name: Optional[str] = None
    surname: Optional[str] = None
    nickname: Optional[str] = None
    date_of_birth: Optional[date] = None
    home_course: Optional[str] = None
    england_golf_number: Optional[str] = None
    phone_number: Optional[str] = None
    # 't3g' or 'manual' -- which handicap counts as "yours" everywhere
    # that doesn't have its own more specific override (a round or
    # tournament entry with its own handicap_source set). See
    # backend/services/handicaps.py's get_effective_handicap_source.
    preferred_handicap_source: Optional[str] = None


class PlayerResponse(BaseModel):
    id: str
    first_name: str
    surname: str
    nickname: Optional[str] = None
    date_of_birth: Optional[date] = None
    home_course: Optional[str] = None
    england_golf_number: Optional[str] = None
    phone_number: Optional[str] = None
    profile_picture_url: Optional[str] = None
    preferred_handicap_source: str = "t3g"
    created_at: str