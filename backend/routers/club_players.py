# target path: backend/routers/club_players.py (new file -- replaces backend/routers/group_players.py, which should be deleted)
from fastapi import APIRouter, HTTPException, status

from backend.models.club_player import (
    ClubPlayerCreate,
    ClubPlayerDelete,
    ClubPlayerResponse,
)
from backend.services.club_players import (
    add_player_to_club,
    list_clubs_for_player,
    list_players_in_club,
    remove_player_from_club,
)


router = APIRouter(
    prefix="/club-players",
    tags=["club players"],
)


@router.post("/", response_model=ClubPlayerResponse, status_code=status.HTTP_201_CREATED)
def add_player_to_club_route(club_player: ClubPlayerCreate):
    return add_player_to_club(club_player)


@router.get("/club/{club_id}")
def list_players_in_club_route(club_id: str):
    return list_players_in_club(club_id)


@router.get("/player/{player_id}")
def list_clubs_for_player_route(player_id: str):
    return list_clubs_for_player(player_id)


@router.delete("/", response_model=ClubPlayerResponse)
def remove_player_from_club_route(club_player: ClubPlayerDelete):
    removed_club_player = remove_player_from_club(club_player)

    if not removed_club_player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player is not in that club",
        )

    return removed_club_player