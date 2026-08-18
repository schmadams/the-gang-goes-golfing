# target path: backend/routers/tournaments.py (full replacement)
from fastapi import APIRouter, HTTPException, status

from backend.models.tournament import (
    TeeTimeAssignmentRequest,
    TeeTimeGenerateRequest,
    TeeTimeUpdateRequest,
    TournamentCreate,
    TournamentEntrantCreate,
    TournamentUpdate,
)
from backend.services.tournament_entrants import (
    AlreadyEnteredError,
    HandicapOutOfRangeError,
    NotClubAdminError as EntrantNotClubAdminError,
    TournamentNotFoundError as EntrantTournamentNotFoundError,
    admin_add_entrant,
    admin_remove_entrant,
    approve_entrant,
    enter_tournament,
    list_entrants_for_tournament,
    reject_entrant,
    withdraw_entrant,
)
from backend.services.tournament_tee_times import (
    InvalidTeeTimeSlotError,
    NoConfirmedEntrantsError,
    NoTeeTimeSlotsError,
    NotClubAdminError as TeeTimeNotClubAdminError,
    RoundNotFoundError,
    TeeTimeSlotNotFoundError,
    assign_tee_time_players,
    generate_tee_times,
    update_tee_time_slot,
)
from backend.services.tournaments import (
    ClubNotFoundError,
    InvalidEntryModeError,
    InvalidFormatError,
    InvalidGroupingMethodError,
    NoRoundsError,
    NotClubAdminError,
    TournamentNotFoundError,
    TournamentRoundNotFoundError,
    create_tournament,
    get_tournament,
    get_tournament_leaderboard,
    list_tournaments_for_club,
    update_tournament,
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
    except (InvalidFormatError, InvalidEntryModeError, InvalidGroupingMethodError, NoRoundsError) as exc:
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


@router.patch("/{tournament_id}")
def update_tournament_route(tournament_id: str, payload: TournamentUpdate):
    try:
        return update_tournament(tournament_id, payload)
    except TournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (InvalidFormatError, InvalidEntryModeError, InvalidGroupingMethodError, NoRoundsError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/{tournament_id}/rounds/{round_id}/tee-times/generate")
def generate_tee_times_route(tournament_id: str, round_id: str, payload: TeeTimeGenerateRequest):
    # tournament_id in the path is purely for a consistent/readable URL --
    # round_id alone is what the service looks up by (a round only ever
    # belongs to one tournament).
    try:
        return generate_tee_times(round_id, payload)
    except RoundNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except TeeTimeNotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except NoConfirmedEntrantsError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.patch("/{tournament_id}/rounds/{round_id}/tee-times/assignments")
def assign_tee_time_players_route(tournament_id: str, round_id: str, payload: TeeTimeAssignmentRequest):
    try:
        return assign_tee_time_players(round_id, payload)
    except RoundNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except TeeTimeNotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (NoTeeTimeSlotsError, InvalidTeeTimeSlotError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


# NOTE: this must stay registered AFTER /tee-times/assignments above --
# FastAPI/Starlette matches routes in registration order, not by
# specificity, and {tee_time_id} as a path segment would happily match the
# literal string "assignments" too. Registering the literal route first
# means it's tried (and matches) before this more general one ever gets a
# chance to swallow that request.
@router.patch("/{tournament_id}/rounds/{round_id}/tee-times/{tee_time_id}")
def update_tee_time_slot_route(tournament_id: str, round_id: str, tee_time_id: str, payload: TeeTimeUpdateRequest):
    try:
        return update_tee_time_slot(tee_time_id, payload)
    except (RoundNotFoundError, TeeTimeSlotNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except TeeTimeNotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


@router.get("/{tournament_id}/leaderboard")
def get_tournament_leaderboard_route(tournament_id: str, round_id: str):
    # round_id is required -- the frontend always has one (defaulted from
    # the tournament's own round list, see tournament.py's
    # _default_leaderboard_round) before this is ever called, so there's
    # no server-side "which round" guesswork to duplicate here.
    try:
        return get_tournament_leaderboard(tournament_id, round_id)
    except TournamentRoundNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


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


@router.post("/{tournament_id}/entrants/{player_id}/add")
def admin_add_entrant_route(tournament_id: str, player_id: str, admin_id: str):
    try:
        return admin_add_entrant(tournament_id, player_id, admin_id)
    except EntrantTournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except EntrantNotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except AlreadyEnteredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.delete("/{tournament_id}/entrants/{player_id}/admin")
def admin_remove_entrant_route(tournament_id: str, player_id: str, admin_id: str):
    try:
        updated = admin_remove_entrant(tournament_id, player_id, admin_id)
    except EntrantTournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except EntrantNotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entrant not found")
    return updated