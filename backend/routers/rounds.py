# target path: backend/routers/rounds.py (full replacement)
from fastapi import APIRouter, HTTPException, status

from backend.models.round import (
    HoleScoreUpdate,
    RoundAnalysisPoint,
    RoundDetailResponse,
    RoundInviteResponse,
    RoundStartRequest,
    RoundSummaryResponse,
)
from backend.services.rounds import (
    ManualScorecardValidationError,
    NotFriendsError,
    RoundAlreadyActiveError,
    RoundInviteNotFoundError,
    TooManyInvitesError,
    delete_round,
    finish_round,
    get_active_round,
    get_player_analysis,
    get_round,
    list_pending_round_invites,
    list_player_rounds,
    respond_to_round_invite,
    start_round,
    update_hole_score,
)

router = APIRouter(prefix="/rounds", tags=["rounds"])


@router.post("/", response_model=RoundDetailResponse, status_code=status.HTTP_201_CREATED)
def start_round_route(payload: RoundStartRequest):
    try:
        round_ = start_round(payload.model_dump())
    except RoundAlreadyActiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except (TooManyInvitesError, NotFriendsError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return round_


@router.get("/active/{player_id}", response_model=RoundDetailResponse)
def get_active_round_route(player_id: str):
    round_ = get_active_round(player_id)
    if not round_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active round")
    return round_


@router.get("/player/{player_id}", response_model=list[RoundSummaryResponse])
def list_player_rounds_route(player_id: str):
    return list_player_rounds(player_id)


@router.get("/player/{player_id}/analysis", response_model=list[RoundAnalysisPoint])
def get_player_analysis_route(player_id: str, window: int = 5):
    return get_player_analysis(player_id, window=window)


@router.get("/invites/{player_id}", response_model=list[RoundInviteResponse])
def list_pending_round_invites_route(player_id: str):
    return list_pending_round_invites(player_id)


@router.post("/{round_id}/invites/{player_id}/accept", response_model=RoundDetailResponse)
def accept_round_invite_route(round_id: str, player_id: str):
    try:
        return respond_to_round_invite(round_id, player_id, accept=True)
    except RoundInviteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RoundAlreadyActiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/{round_id}/invites/{player_id}/decline", response_model=RoundDetailResponse)
def decline_round_invite_route(round_id: str, player_id: str):
    try:
        return respond_to_round_invite(round_id, player_id, accept=False)
    except RoundInviteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.patch("/{round_id}/players/{player_id}/holes/{hole_number}", response_model=RoundDetailResponse)
def update_hole_score_route(round_id: str, player_id: str, hole_number: int, payload: HoleScoreUpdate):
    round_ = update_hole_score(round_id, player_id, hole_number, payload.model_dump(exclude_unset=True))
    if not round_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round or hole not found")
    return round_


@router.post("/{round_id}/finish", response_model=RoundDetailResponse)
def finish_round_route(round_id: str):
    try:
        round_ = finish_round(round_id)
    except ManualScorecardValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if not round_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round not found")
    return round_


@router.get("/{round_id}", response_model=RoundDetailResponse)
def get_round_route(round_id: str):
    round_ = get_round(round_id)
    if not round_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round not found")
    return round_


@router.delete("/{round_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_round_route(round_id: str):
    # Same endpoint serves two UI actions -- "Scrap Round" on an
    # in-progress round from the live round page, and "Delete" on a
    # completed round from Scoring History. Both are just "this round row
    # (and its scores) shouldn't exist anymore."
    deleted = delete_round(round_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round not found")