# target path: backend/routers/handicaps.py (full replacement)
from fastapi import APIRouter, HTTPException, status

from backend.models.handicap import HandicapCreate, HandicapResponse
from backend.services.handicaps import (
    add_player_handicap,
    get_current_player_handicap,
    get_handicap_breakdown,
    get_player_handicap_sources,
    list_latest_handicaps_for_club,
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


@router.get("/player/{player_id}/sources")
def get_player_handicap_sources_route(player_id: str):
    """Both the T3G (WHS-calculated) and manually-entered current
    handicap values side by side, plus which one this player has set as
    their default -- see get_player_handicap_sources's own docstring."""
    return get_player_handicap_sources(player_id)


@router.get("/club/{club_id}/latest")
def list_latest_handicaps_for_club_route(club_id: str):
    return list_latest_handicaps_for_club(club_id)


@router.get("/player/{player_id}/breakdown")
def get_handicap_breakdown_route(player_id: str):
    """Full scoring-record breakdown behind the current Handicap Index --
    every round in the most-recent-20 window, tagged with whether it's
    one of the "lowest N" currently being averaged. Powers the home
    page's "Contributing Rounds" view."""
    return get_handicap_breakdown(player_id)