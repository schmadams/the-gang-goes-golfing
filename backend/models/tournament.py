# target path: backend/models/tournament.py (full replacement)
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel

# Kept as a plain set (not a pydantic/Python Enum) so the service layer can
# raise a friendly InvalidFormatError with the full valid list in its
# message, same style as club_invites.py's custom exceptions -- an Enum
# would just 422 with FastAPI's generic validation error instead.
VALID_TOURNAMENT_FORMATS = {"scratch", "stableford", "net", "2bbb", "4bbb", "texas_scramble"}
VALID_ENTRY_MODES = {"self", "approval"}


class TournamentRoundCreate(BaseModel):
    round_date: date
    course_id: UUID
    tee_id: UUID


class TournamentCreate(BaseModel):
    club_id: UUID
    admin_id: UUID  # must match clubs.club_admin -- enforced in the service layer
    name: str
    format: str
    rounds: list[TournamentRoundCreate]
    entry_mode: str = "self"  # "self" (join directly) or "approval" (admin reviews each application)
    min_handicap: float | None = None
    max_handicap: float | None = None


class TournamentUpdate(BaseModel):
    admin_id: UUID  # must match clubs.club_admin -- enforced in the service layer
    name: str
    format: str
    rounds: list[TournamentRoundCreate]
    entry_mode: str = "self"
    min_handicap: float | None = None
    max_handicap: float | None = None


class TournamentRoundResponse(BaseModel):
    id: UUID
    tournament_id: UUID
    round_number: int
    round_date: date
    course_id: UUID
    tee_id: UUID
    club_name: str | None = None
    course_name: str | None = None
    tee_name: str | None = None


class TournamentEntrantCreate(BaseModel):
    player_id: UUID


class TournamentEntrantResponse(BaseModel):
    id: UUID
    tournament_id: UUID
    player_id: UUID
    status: str
    handicap_at_entry: float | None = None
    created_at: datetime
    responded_at: datetime | None = None
    first_name: str | None = None
    surname: str | None = None
    nickname: str | None = None


class TournamentResponse(BaseModel):
    id: UUID
    club_id: UUID
    name: str
    format: str
    status: str
    entry_mode: str
    min_handicap: float | None = None
    max_handicap: float | None = None
    created_by: UUID
    created_at: datetime
    rounds: list[TournamentRoundResponse] = []
    entrants: list[TournamentEntrantResponse] = []