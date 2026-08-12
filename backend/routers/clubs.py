# target path: backend/routers/clubs.py (new file -- replaces backend/routers/groups.py, which should be deleted)
from fastapi import APIRouter, HTTPException, status

from backend.models.club import ClubCreate, ClubResponse
from backend.services.clubs import (
    DuplicateClubError,
    create_club,
    delete_club,
    list_clubs,
)


router = APIRouter(
    prefix="/clubs",
    tags=["clubs"],
)


@router.get("/", response_model=list[ClubResponse])
def list_clubs_route():
    return list_clubs()


@router.post("/", response_model=ClubResponse, status_code=status.HTTP_201_CREATED)
def create_club_route(club: ClubCreate):
    try:
        return create_club(club)
    except DuplicateClubError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.delete("/{club_id}", response_model=ClubResponse)
def delete_club_route(club_id: str):
    deleted_club = delete_club(club_id)

    if not deleted_club:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Club not found",
        )

    return deleted_club