# target path: backend/routers/tournaments.py (full replacement)
from fastapi import APIRouter, HTTPException, status

from backend.models.tournament import TournamentCreate, TournamentEntrantCreate
from backend.services.tournament_entrants import (
    AlreadyEnteredError,
    HandicapOutOfRangeError,
    NotClubAdminError as EntrantNotClubAdminError,
    TournamentNotFoundError as EntrantTournamentNotFoundError,
    approve_entrant,
    enter_tournament,
    list_entrants_for_tournament,
    reject_entrant,
    withdraw_entrant,
)
from backend.services.tournaments import (
    ClubNotFoundError,
    InvalidEntryModeError,
    InvalidFormatError,
    NoRoundsError,
    NotClubAdminError,
    create_tournament,
    get_tournament,
    list_tournaments_for_club,
)

router = APIRouter(prefix="/tournaments", tags=["tournaments"])


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_tournament_route(payload: TournamentCreate):
    try:
        return create_tournament(payload)
    except ClubNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (InvalidFormatError, InvalidEntryModeError, NoRoundsError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/club/{club_id}")
def list_tournaments_for_club_route(club_id: str):
    return list_tournaments_for_club(club_id)


@router.get("/{tournament_id}")
def get_tournament_route(tournament_id: str):
    tournament = get_tournament(tournament_id)
    if not tournament:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")
    return tournament


@router.get("/{tournament_id}/entrants")
def list_entrants_route(tournament_id: str):
    return list_entrants_for_tournament(tournament_id)


@router.post("/{tournament_id}/entrants", status_code=status.HTTP_201_CREATED)
def enter_tournament_route(tournament_id: str, payload: TournamentEntrantCreate):
    try:
        return enter_tournament(tournament_id, str(payload.player_id))
    except EntrantTournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except AlreadyEnteredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except HandicapOutOfRangeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/{tournament_id}/entrants/{player_id}/approve")
def approve_entrant_route(tournament_id: str, player_id: str, admin_id: str):
    try:
        updated = approve_entrant(tournament_id, player_id, admin_id)
    except EntrantTournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except EntrantNotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entrant not found")
    return updated


@router.post("/{tournament_id}/entrants/{player_id}/reject")
def reject_entrant_route(tournament_id: str, player_id: str, admin_id: str):
    try:
        updated = reject_entrant(tournament_id, player_id, admin_id)
    except EntrantTournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except EntrantNotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entrant not found")
    return updated


@router.delete("/{tournament_id}/entrants/{player_id}")
def withdraw_entrant_route(tournament_id: str, player_id: str):
    updated = withdraw_entrant(tournament_id, player_id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entrant not found")
    return updated