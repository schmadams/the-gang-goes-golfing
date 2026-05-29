from fastapi import APIRouter, HTTPException, status

from backend.models.group import GroupCreate, GroupResponse
from backend.services.groups import create_group, delete_group, list_groups


router = APIRouter(
    prefix="/groups",
    tags=["groups"],
)


@router.get("/", response_model=list[GroupResponse])
def list_groups_route():
    return list_groups()


@router.post("/", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group_route(group: GroupCreate):
    return create_group(group)


@router.delete("/{group_id}", response_model=GroupResponse)
def delete_group_route(group_id: str):
    deleted_group = delete_group(group_id)

    if not deleted_group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    return deleted_group