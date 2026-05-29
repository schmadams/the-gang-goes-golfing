from fastapi import APIRouter, status
from pydantic import BaseModel
from datetime import date, datetime
from uuid import UUID

from backend.database import supabase

router = APIRouter(
    prefix="/players",
    tags=["players"]
)

class PlayerCreate(BaseModel):
    first_name: str
    surname: str
    date_of_birth: date | None = None


class PlayerResponse(BaseModel):
    id: UUID
    first_name: str
    surname: str
    date_of_birth: date | None = None
    created_at: datetime | None = None

@router.post("/", response_model=PlayerResponse, status_code=201)
def add_player(player: PlayerCreate):
    """Add a new player to the players table."""
    response = (
        supabase.table("players")
        .insert({
            "first_name": player.first_name,
            "surname": player.surname,
            "date_of_birth": str(player.date_of_birth) if player.date_of_birth else None
        })
        .execute()
    )

    if not response.data:
        raise HTTPException(status_code=500, detail="Failed to insert player")

    return response.data[0]


@router.get("/", response_model=list[PlayerResponse])
def get_players():
    """Get all players."""
    response = supabase.table("players").select("*").execute()
    return response.data


@router.get("/")
def list_players():
    response = (
        supabase
        .table("players")
        .select("*")
        .order("created_at")
        .execute()
    )

    return response.data


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_player(player: PlayerCreate):
    payload = {
        "first_name": player.first_name,
        "surname": player.surname,
        "date_of_birth": player.date_of_birth.isoformat() if player.date_of_birth else None,
    }

    response = (
        supabase
        .table("players")
        .insert(payload)
        .execute()
    )

    return response.data[0]