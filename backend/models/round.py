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
    # No club_id here any more -- a casual round used to need to be
    # manually tagged with a club to count toward that club's player
    # comparison analysis (see add_round_club_id.sql). That's gone now:
    # get_club_player_comparison figures out which clubs a round belongs
    # to itself, automatically, by checking which of the round's players
    # are members of which club -- no tag, no dropdown, nothing for the
    # player starting the round to think about. See that function's own
    # docstring in backend/services/rounds.py for exactly how.

    @field_validator("invited_player_ids")
    @classmethod
    def validate_invited_player_ids(cls, v):
        if len(v) > 3:
            raise ValueError("You can only add up to 3 other friends to a round.")
        return v


class TournamentRoundStartRequest(BaseModel):
    """Starting (or joining, if a groupmate already beat you to it) the
    shared live round for one tournament tee time grouping -- see
    start_tournament_round in backend/services/rounds.py. Deliberately a
    separate, much smaller request than RoundStartRequest: course/tee come
    from the tournament round itself, players come from the tee time's own
    assignments, so there's no course/tee/manual/invited_player_ids to
    supply here, just who's asking."""
    player_id: str


class HoleScoreUpdate(BaseModel):
    """
    Partial update for a single player's single hole. The router calls
    payload.model_dump(exclude_unset=True), so only fields actually sent
    get written -- e.g. saving a score doesn't clobber manual_par etc back
    to null.

    nr (No Return) marks this hole as not completed/recorded, instead of a
    real stroke count -- the live scorecard's score modal sends
    {"nr": True, "strokes": None, "putts": None, "fairway_hit": None} for
    its dedicated "NR" save action (tournament rounds only), and sends
    {"nr": False, ...real values...} for a normal "Enter" save so a
    previously-NR'd hole gets cleared the moment a real score is entered
    again -- there's no separate "undo NR" action, re-scoring the hole
    just is the undo. See mark_round_no_result in backend/services/
    rounds.py for the bulk "NR the whole round" version of this same idea.
    """
    strokes: Optional[int] = None
    putts: Optional[int] = None
    fairway_hit: Optional[bool] = None
    nr: Optional[bool] = None
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
    nr: bool = False
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
    # When this player approved the round's scorecard while it was
    # pending_signoff -- None means either the round never needed
    # sign-off (solo round, straight to completed) or this player hasn't
    # signed off yet. See backend/services/rounds.py sign_off_round /
    # reject_round_signoff and add_round_signoff.sql.
    signed_off_at: Optional[datetime] = None
    holes: list[HoleScoreResponse] = []


class RoundResponse(BaseModel):
    id: UUID
    player_id: UUID  # the round's owner/starter
    course_id: Optional[UUID] = None
    tee_id: Optional[UUID] = None
    is_manual: bool
    manual_club_name: Optional[str] = None
    manual_tee_name: Optional[str] = None
    # 'in_progress' -> (for a round with more than one accepted player)
    # 'pending_signoff' -> 'completed', or 'in_progress' -> 'completed'
    # directly for a solo round -- see finish_round / sign_off_round in
    # backend/services/rounds.py. 'pending_signoff' still feeds the live
    # tournament leaderboard exactly like 'in_progress'/'completed' always
    # have (get_tournament_leaderboard reads round_scores directly, with
    # no status filtering at all), it just isn't in anyone's Handicap
    # Index yet and blocks that tournament's next round from starting
    # until it clears.
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    # Relative to whichever player_id the lookup was made for (e.g.
    # get_active_round(player_id)) -- lets the frontend know whether the
    # viewer can Finish/Scrap this round.
    is_owner: Optional[bool] = None
    # Both set together, or both null -- see rounds_tournament_link_
    # consistent in add_tournament_live_rounds.sql. Non-null means this is
    # a tournament round: started from a specific tee time grouping on the
    # Start Sheet, rather than a normal casual round.
    tournament_round_id: Optional[UUID] = None
    tee_time_id: Optional[UUID] = None
    # Populated alongside the two above (see _tournament_context_for_round
    # in backend/services/rounds.py) -- lets the live round page link back
    # into the tournament (its own subnav, Return to Club) instead of
    # being a dead end once you're scoring, and lets any round display
    # (home page's Live Round panel, Rounds History, round_header_label)
    # show which tournament and round number this is -- the only thing
    # that actually distinguishes a tournament round from a casual one at
    # a glance, now that a player can have one of each in progress at
    # once.
    tournament_id: Optional[UUID] = None
    tournament_name: Optional[str] = None
    tournament_round_number: Optional[int] = None
    club_slug: Optional[str] = None


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
    nr: bool = False
    net_strokes: Optional[int] = None
    stableford_points: Optional[int] = None


class RoundSummaryResponse(RoundResponse):
    """Shape for the Rounds History panel and Scoring History page --
    always the *viewing* player's own scorecard within a round they own or
    were an accepted participant in, not everyone's. A full mini scorecard
    (par/strokes/putts/fairway/net/Stableford per hole) plus round-level
    totals. Covers in-progress, pending_signoff, and completed rounds the
    player belongs to (status distinguishes them, inherited from
    RoundResponse) so a live or awaiting-signoff round can show up in the
    same list, marked accordingly, instead of needing a separate lookup."""
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