# target path: backend/routers/players.py (full replacement)
from fastapi import APIRouter, File, HTTPException, UploadFile, status

from backend.models.player import PlayerCreate, PlayerResponse, PlayerUpdate
from backend.services.players import (
    create_player,
    get_player,
    list_players,
    update_player,
    upload_profile_picture,
)

router = APIRouter(
    prefix="/players",
    tags=["players"],
)


@router.get("/", response_model=list[PlayerResponse])
def list_players_route():
    return list_players()


@router.post("/", response_model=PlayerResponse, status_code=status.HTTP_201_CREATED)
def create_player_route(player: PlayerCreate):
    return create_player(
        player.first_name,
        player.surname,
        player.date_of_birth.isoformat() if player.date_of_birth else None,
    )


@router.get("/{player_id}", response_model=PlayerResponse)
def get_player_route(player_id: str):
    player = get_player(player_id)

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Player not found"
        )

    return player


@router.patch("/{player_id}", response_model=PlayerResponse)
def update_player_route(player_id: str, player: PlayerUpdate):
    payload = player.model_dump(exclude_unset=True)

    if payload.get("date_of_birth") is not None:
        payload["date_of_birth"] = payload["date_of_birth"].isoformat()

    updated = update_player(player_id, payload)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Player not found"
        )

    return updated


@router.post("/{player_id}/profile-picture", response_model=PlayerResponse)
async def upload_profile_picture_route(player_id: str, file: UploadFile = File(...)):
    file_bytes = await file.read()
    updated = upload_profile_picture(player_id, file_bytes, file.filename, file.content_type)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Player not found"
        )

    return updated