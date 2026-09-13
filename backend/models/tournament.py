# target path: backend/models/tournament.py (full replacement)
from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel

# Kept as a plain set (not a pydantic/Python Enum) so the service layer can
# raise a friendly InvalidFormatError with the full valid list in its
# message, same style as club_invites.py's custom exceptions -- an Enum
# would just 422 with FastAPI's generic validation error instead.
VALID_TOURNAMENT_FORMATS = {"scratch", "stableford", "net", "2bbb", "4bbb", "texas_scramble"}
VALID_ENTRY_MODES = {"self", "approval"}
# How confirmed entrants get sorted into tee time groups when they're
# generated -- "random" shuffles the field, "handicap" sorts ascending by
# handicap_at_entry (lowest/best first) before chunking into groups,
# "manual" skips auto-assignment entirely: generating just creates the
# right number of empty slots (sized from group_size) for the admin to
# place entrants into themselves via assign_tee_time_players.
VALID_GROUPING_METHODS = {"random", "handicap", "manual"}
# The standard golf-competition "handicap limit" allowance -- how much
# of a player's full handicap counts for scoring in this tournament.
# Distinct from min_handicap/max_handicap above, which gate entry
# eligibility rather than scoring.
VALID_HANDICAP_ALLOWANCES = {50, 75, 100}


class TournamentRoundCreate(BaseModel):
    round_date: date
    course_id: UUID
    tee_id: UUID
    # Players per tee time group for this round -- comp spec, set at
    # creation/edit time. A comp might run 3-balls one week, 4-balls the
    # next, so this lives per round rather than on the tournament itself.
    group_size: int = 4


class TournamentCreate(BaseModel):
    club_id: UUID
    admin_id: UUID  # must match clubs.club_admin -- enforced in the service layer
    name: str
    format: str
    # Empty is only valid when linked_tournament_id is set below -- a
    # *shadow* tournament owns no rounds of its own (its linked
    # tournament's rounds are what it actually plays), so create_tournament
    # only enforces "at least one round" when this isn't a shadow. See
    # linked_tournament_id's own comment.
    rounds: list[TournamentRoundCreate] = []
    entry_mode: str = "self"  # "self" (join directly) or "approval" (admin reviews each application)
    min_handicap: float | None = None
    max_handicap: float | None = None
    grouping_method: str = "random"
    handicap_allowance: int = 100
    # Set this to make the new tournament a *shadow* of an existing one --
    # e.g. a Pairs Better Ball event run over the same field and rounds as
    # an already-set-up individual Stableford event. A shadow tournament
    # has no entrants/rounds/tee-times of its own; every read and write
    # for those transparently resolves to the linked tournament instead
    # (see backend/services/tournaments.py's _resolve_source_tournament_id).
    # None (the default) is a normal, fully independent tournament. Must
    # name a tournament in the same club that isn't itself a shadow and
    # doesn't already have a shadow of its own -- see create_tournament's
    # validation.
    linked_tournament_id: UUID | None = None


class TournamentUpdate(BaseModel):
    admin_id: UUID  # must match clubs.club_admin -- enforced in the service layer
    name: str
    format: str
    rounds: list[TournamentRoundCreate] = []
    entry_mode: str = "self"
    min_handicap: float | None = None
    max_handicap: float | None = None
    grouping_method: str = "random"
    handicap_allowance: int = 100
    # Same meaning as TournamentCreate.linked_tournament_id -- editable
    # after creation too (link a tournament to another later, or clear
    # this back to None to unlink and make it independent again). See
    # update_tournament's validation for what's allowed to change here.
    linked_tournament_id: UUID | None = None


class TeeTimeGenerateRequest(BaseModel):
    admin_id: UUID  # must match clubs.club_admin -- enforced in the service layer
    first_tee_time: time


class TeeTimeAssignmentRequest(BaseModel):
    admin_id: UUID  # must match clubs.club_admin -- enforced in the service layer
    # player_id -> tee_time_id (the specific group/slot they're placed in),
    # or None to leave/make them unassigned. Sent as the full set of
    # confirmed entrants every time (same "replace, don't diff" approach as
    # update_tournament's rounds and generate_tee_times itself) rather than
    # a partial patch, so the dropdown state on the page is always exactly
    # what ends up saved.
    assignments: dict[str, str | None]


class TeeTimeUpdateRequest(BaseModel):
    admin_id: UUID  # must match clubs.club_admin -- enforced in the service layer
    tee_time: time


class TournamentRoundResponse(BaseModel):
    id: UUID
    tournament_id: UUID
    round_number: int
    round_date: date
    course_id: UUID
    tee_id: UUID
    group_size: int = 4
    club_name: str | None = None
    course_name: str | None = None
    tee_name: str | None = None


class TournamentEntrantHandicapOverrideUpdate(BaseModel):
    # A replacement for this entrant's full handicap (not their Comp.
    # Hcp) -- the tournament's allowance % still applies on top when
    # scoring, same as every entrant's handicap normally would. None
    # clears an existing override, reverting this entrant to the normal
    # live-lookup handicap -- same "omitted/None means fall back to the
    # default behavior" convention as TournamentEntrantCreate.handicap_source
    # below.
    handicap_override: float | None = None


class TournamentEntrantCreate(BaseModel):
    player_id: UUID
    # 't3g' or 'manual', or omitted -- which handicap this player wants
    # used for the tournament's min/max entry gate and for every net/
    # Stableford calculation of theirs throughout the tournament.
    # Omitted means "use my account preference" (see backend/services/
    # handicaps.py's get_effective_handicap_source). Captured once at
    # entry, alongside handicap_at_entry, and stays fixed for this
    # tournament even if the player's account preference changes later --
    # same reasoning as handicap_at_entry itself.
    handicap_source: str | None = None


class TournamentEntrantResponse(BaseModel):
    id: UUID
    tournament_id: UUID
    player_id: UUID
    status: str
    handicap_at_entry: float | None = None
    handicap_source: str | None = None
    # Admin-set override for this entrant's full handicap, if any -- the
    # tournament's own handicap_allowance % still applies on top to get
    # their Comp. Hcp, same as every other entrant. See the migration
    # comment on tournament_entrants.handicap_override.
    handicap_override: float | None = None
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
    grouping_method: str = "random"
    handicap_allowance: int = 100
    created_by: UUID
    created_at: datetime
    rounds: list[TournamentRoundResponse] = []
    entrants: list[TournamentEntrantResponse] = []
    # This tournament's own outgoing link, if it's a shadow of another
    # (see TournamentCreate.linked_tournament_id) -- name included
    # alongside the id so the page can render "Linked to: <name>" without
    # a second fetch.
    linked_tournament_id: UUID | None = None
    linked_tournament_name: str | None = None
    # The reverse direction -- some *other* tournament that's a shadow of
    # this one, if any, so the source tournament's own page can link
    # across to its pairs (or whatever) companion too. At most one in
    # practice (see create_tournament's validation), but this is metadata
    # only; nothing about data resolution depends on it.
    linked_from_tournament_id: UUID | None = None
    linked_from_tournament_name: str | None = None


class TournamentPairsSetRequest(BaseModel):
    admin_id: UUID  # must match clubs.club_admin -- enforced in the service layer
    # Full replacement of every pair for this tournament, same "resubmit
    # the whole set" convention as TeeTimeAssignmentRequest.assignments --
    # each inner 2-item list is one pair's [player_id_a, player_id_b].
    pairs: list[list[UUID]]


class TournamentPairResponse(BaseModel):
    id: UUID
    tournament_id: UUID
    player_id_a: UUID
    player_id_b: UUID
    player_a_name: str | None = None
    player_b_name: str | None = None