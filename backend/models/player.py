from pydantic import BaseModel
from typing import Optional
from datetime import date


class PlayerCreate(BaseModel):
    first_name: str
    surname: str
    date_of_birth: Optional[date] = None


class PlayerResponse(BaseModel):
    id: str
    first_name: str
    surname: str
    date_of_birth: Optional[date] = None
    created_at: str