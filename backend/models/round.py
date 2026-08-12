# target path: backend/models/round.py (full replacement)
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
    # Optional -- without these, a manual round can still be played and
    # scored, but it can never contribute a WHS Score Differential (no
    # slope/rating means no differential formula), so it just won't count
    # toward the player's Handicap Index. Typically found printed on the
    # scorecard next to the tee colour.
    manual_course_rating: Optional[float] = None
    manual_slope_rating: Optional[int] = None
    # Up to 3 confirmed friends invited into the round alongside you --
    # each gets a round_players row with status='invited' and has to
    # accept before their scorecard exists (see backend/services/rounds.py
    # start_round / respond_to_round_invite).
    invited_player_ids: list[str] = []

    @field_validator("invited_player_ids")
    @classmethod
    def validate_invited_player_ids(cls, v):
        if len(v) > 3:
            raise ValueError("You can only add up to 3 other friends to a round.")
        return v


class HoleScoreUpdate(BaseModel):
    """
    Partial update for a single player's single hole. The router calls
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


class RoundPlayerScorecard(BaseModel):
    """One participant's scorecard within a live/detail round view --
    accepted participants carry all 18 holes; invited-but-not-yet-responded
    players show up in `pending_invites` on RoundDetailResponse instead,
    without a scorecard (they don't have one yet)."""
    player_id: UUID
    is_owner: bool
    status: str
    first_name: Optional[str] = None
    surname: Optional[str] = None
    nickname: Optional[str] = None
    holes: list[HoleScoreResponse] = []


class RoundResponse(BaseModel):
    id: UUID
    player_id: UUID  # the round's owner/starter
    course_id: Optional[UUID] = None
    tee_id: Optional[UUID] = None
    is_manual: bool
    manual_club_name: Optional[str] = None
    manual_tee_name: Optional[str] = None
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    # Relative to whichever player_id the lookup was made for (e.g.
    # get_active_round(player_id)) -- lets the frontend know whether the
    # viewer can Finish/Scrap this round.
    is_owner: Optional[bool] = None


class RoundDetailResponse(RoundResponse):
    club_name: Optional[str] = None
    course_name: Optional[str] = None
    tee_name: Optional[str] = None
    players: list[RoundPlayerScorecard] = []
    pending_invites: list[RoundPlayerScorecard] = []


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
    """Shape for the Rounds History panel and Scoring History page --
    always the *viewing* player's own scorecard within a round they own or
    were an accepted participant in, not everyone's. A full mini scorecard
    (par/strokes/putts/fairway/net/Stableford per hole) plus round-level
    totals. Covers both completed rounds and any in-progress round the
    player belongs to (status distinguishes them, inherited from
    RoundResponse) so a live round can show up in the same list, marked as
    live, instead of needing a separate lookup."""
    club_name: Optional[str] = None
    course_name: Optional[str] = None
    tee_name: Optional[str] = None
    total_strokes: Optional[int] = None
    holes_played: int = 0
    holes: list[RoundHoleSummary] = []
    handicap: Optional[float] = None
    total_stableford: Optional[int] = None


class RoundAnalysisPoint(BaseModel):
    """One completed round's contribution to the Player Analysis charts --
    its own totals plus the trailing rolling average as of that round."""
    date: str
    putts_total: Optional[int] = None
    putts_rolling_avg: Optional[float] = None
    fairway_pct: Optional[float] = None
    fairway_rolling_avg: Optional[float] = None


class RoundInviteResponse(BaseModel):
    """A pending invite into someone else's round, for the notification
    list on the home page / navbar."""
    round_id: UUID
    player_id: UUID
    is_owner: bool
    status: str
    invited_at: datetime
    owner_first_name: Optional[str] = None
    owner_surname: Optional[str] = None
    club_name: Optional[str] = None
    course_name: Optional[str] = None