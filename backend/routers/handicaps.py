from fastapi import APIRouter, HTTPException, status

from backend.models.handicap import HandicapCreate, HandicapResponse
from backend.services.handicaps import (
    add_player_handicap,
    get_current_player_handicap,
list_latest_handicaps_for_group,
    list_player_handicaps,
)




router = APIRouter(
    prefix="/handicaps",
    tags=["handicaps"],
)


@router.post("/", response_model=HandicapResponse, status_code=status.HTTP_201_CREATED)
def add_player_handicap_route(handicap: HandicapCreate):
    return add_player_handicap(handicap)


@router.get("/player/{player_id}", response_model=list[HandicapResponse])
def list_player_handicaps_route(player_id: str):
    return list_player_handicaps(player_id)


@router.get("/player/{player_id}/current", response_model=HandicapResponse)
def get_current_player_handicap_route(player_id: str):
    handicap = get_current_player_handicap(player_id)

    if not handicap:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No handicap found for player",
        )

    return handicap

@router.get("/group/{group_id}/latest")
def list_latest_handicaps_for_group_route(group_id: str):
    return list_latest_handicaps_for_group(group_id)