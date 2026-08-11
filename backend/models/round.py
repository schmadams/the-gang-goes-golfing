# target path: backend/models/round.py (new file)
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


class RoundStartRequest(BaseModel):
    player_id: str
    course_id: Optional[str] = None
    tee_id: Optional[str] = None
    is_manual: bool = False
    manual_club_name: Optional[str] = None
    manual_tee_name: Optional[str] = None


class HoleScoreUpdate(BaseModel):
    """
    Partial update for a single hole. The router calls
    payload.model_dump(exclude_unset=True), so only fields actually sent
    get written -- e.g. saving a score doesn't clobber manual_par etc back
    to null.
    """
    strokes: Optional[int] = None
    putts: Optional[int] = None
    fairway_hit: Optional[bool] = None
    manual_par: Optional[int] = None
    manual_yardage: Optional[int] = None
    manual_stroke_index: Optional[int] = None

    @field_validator("putts")
    @classmethod
    def validate_putts(cls, v):
        if v is not None and not (0 <= v <= 10):
            raise ValueError("Putts must be between 0 and 10")
        return v

    @field_validator("strokes")
    @classmethod
    def validate_strokes(cls, v):
        if v is not None and not (1 <= v <= 15):
            raise ValueError("Strokes must be between 1 and 15")
        return v

    @field_validator("manual_par")
    @classmethod
    def validate_par(cls, v):
        if v is not None and v not in (3, 4, 5):
            raise ValueError("Par must be 3, 4, or 5")
        return v

    @field_validator("manual_yardage")
    @classmethod
    def validate_yardage(cls, v):
        if v is not None and not (0 <= v <= 1000):
            raise ValueError("Hole length must be between 0 and 1000 yards")
        return v

    @field_validator("manual_stroke_index")
    @classmethod
    def validate_stroke_index(cls, v):
        if v is not None and not (1 <= v <= 18):
            raise ValueError("Stroke index must be between 1 and 18")
        return v


class HoleScoreResponse(BaseModel):
    hole_number: int
    strokes: Optional[int] = None
    putts: Optional[int] = None
    fairway_hit: Optional[bool] = None
    manual_par: Optional[int] = None
    manual_yardage: Optional[int] = None
    manual_stroke_index: Optional[int] = None
    # Populated from course_holes when the round is tied to a real tee (not
    # manual, or a manual round that's already been finished) -- lets the
    # frontend show one consistent par/yardage/SI regardless of source.
    par: Optional[int] = None
    yardage: Optional[int] = None
    stroke_index: Optional[int] = None


class RoundResponse(BaseModel):
    id: UUID
    player_id: UUID
    course_id: Optional[UUID] = None
    tee_id: Optional[UUID] = None
    is_manual: bool
    manual_club_name: Optional[str] = None
    manual_tee_name: Optional[str] = None
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None


class RoundDetailResponse(RoundResponse):
    club_name: Optional[str] = None
    course_name: Optional[str] = None
    tee_name: Optional[str] = None
    holes: list[HoleScoreResponse] = []


class RoundHoleSummary(BaseModel):
    """Per-hole shape for the Rounds History / Scoring History pages --
    par + strokes for the traditional birdie/bogey marks, plus putts,
    fairway hit, and the handicap-adjusted net strokes / Stableford points
    for that hole. Not the full manual-entry fields HoleScoreResponse
    carries, since history views never edit a finished round's course
    data."""
    hole_number: int
    par: Optional[int] = None
    stroke_index: Optional[int] = None
    strokes: Optional[int] = None
    putts: Optional[int] = None
    fairway_hit: Optional[bool] = None
    net_strokes: Optional[int] = None
    stableford_points: Optional[int] = None


class RoundSummaryResponse(RoundResponse):
    """Shape for the Rounds History panel and Scoring History page -- a
    full mini scorecard (par/strokes/putts/fairway/net/Stableford per
    hole) plus round-level totals. Covers both completed rounds and the
    single in-progress round (status distinguishes them, inherited from
    RoundResponse) so a live round can show up in the same list, marked
    as live, instead of needing a separate lookup."""
    club_name: Optional[str] = None
    course_name: Optional[str] = None
    tee_name: Optional[str] = None
    total_strokes: Optional[int] = None
    holes_played: int = 0
    holes: list[RoundHoleSummary] = []
    handicap: Optional[float] = None
    total_stableford: Optional[int] = None