from fastapi import APIRouter, HTTPException, status

from backend.models.group_player import (
    GroupPlayerCreate,
    GroupPlayerDelete,
    GroupPlayerResponse,
)
from backend.services.group_players import (
    add_player_to_group,
    list_groups_for_player,
    list_players_in_group,
    remove_player_from_group,
)


router = APIRouter(
    prefix="/group-players",
    tags=["group players"],
)


@router.post("/", response_model=GroupPlayerResponse, status_code=status.HTTP_201_CREATED)
def add_player_to_group_route(group_player: GroupPlayerCreate):
    return add_player_to_group(group_player)


@router.get("/group/{group_id}")
def list_players_in_group_route(group_id: str):
    return list_players_in_group(group_id)


@router.get("/player/{player_id}")
def list_groups_for_player_route(player_id: str):
    return list_groups_for_player(player_id)


@router.delete("/", response_model=GroupPlayerResponse)
def remove_player_from_group_route(group_player: GroupPlayerDelete):
    removed_group_player = remove_player_from_group(group_player)

    if not removed_group_player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player is not in that group",
        )

    return removed_group_player