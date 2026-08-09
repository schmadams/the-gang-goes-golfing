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
    created_at: str