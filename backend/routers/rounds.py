# target path: backend/routers/rounds.py (new file)
from fastapi import APIRouter, HTTPException, status

from backend.models.round import (
    HoleScoreUpdate,
    RoundDetailResponse,
    RoundStartRequest,
)
from backend.services.rounds import (
    ManualScorecardValidationError,
    RoundAlreadyActiveError,
    finish_round,
    get_active_round,
    get_round,
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
    return round_


@router.get("/active/{player_id}", response_model=RoundDetailResponse)
def get_active_round_route(player_id: str):
    round_ = get_active_round(player_id)
    if not round_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active round")
    return round_


@router.patch("/{round_id}/holes/{hole_number}", response_model=RoundDetailResponse)
def update_hole_score_route(round_id: str, hole_number: int, payload: HoleScoreUpdate):
    round_ = update_hole_score(round_id, hole_number, payload.model_dump(exclude_unset=True))
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