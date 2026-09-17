# target path: backend/routers/tournaments.py (full replacement)
from fastapi import APIRouter, HTTPException, status

from backend.models.tournament import (
    TeeTimeAssignmentRequest,
    TeeTimeGenerateRequest,
    TeeTimeUpdateRequest,
    TournamentCreate,
    TournamentEntrantCreate,
    TournamentEntrantHandicapOverrideUpdate,
    TournamentFinalizeRequest,
    TournamentPairsSetRequest,
    TournamentUpdate,
)
from backend.services.tournament_entrants import (
    AlreadyEnteredError,
    HandicapOutOfRangeError,
    InvalidHandicapOverrideError,
    NotClubAdminError as EntrantNotClubAdminError,
    TournamentNotFoundError as EntrantTournamentNotFoundError,
    admin_add_entrant,
    admin_remove_entrant,
    approve_entrant,
    enter_tournament,
    list_entrants_for_tournament,
    reject_entrant,
    set_entrant_handicap_override,
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
    list_scheduled_tee_times_for_player,
    update_tee_time_slot,
)
from backend.services.tournament_pairs import (
    InvalidPairingError,
    NotClubAdminError as PairsNotClubAdminError,
    TournamentNotFoundError as PairsTournamentNotFoundError,
    list_tournament_pairs,
    set_tournament_pairs,
)
from backend.services.tournaments import (
    ClubNotFoundError,
    InvalidEntryModeError,
    InvalidFormatError,
    InvalidGroupingMethodError,
    InvalidHandicapAllowanceError,
    InvalidLinkError,
    InvalidPairsScoringStyleError,
    NoRoundsError,
    NotClubAdminError,
    TournamentAlreadyFinalizedError,
    TournamentNotFoundError,
    TournamentNotReadyToFinalizeError,
    TournamentRoundNotFoundError,
    create_tournament,
    finalize_tournament,
    get_club_tournament_history,
    get_tournament,
    get_tournament_leaderboard,
    get_tournament_pairs_leaderboard,
    get_tournament_pairs_overall_leaderboard,
    get_tournament_winner,
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
    except (
        InvalidFormatError,
        InvalidEntryModeError,
        InvalidGroupingMethodError,
        InvalidHandicapAllowanceError,
        InvalidPairsScoringStyleError,
        NoRoundsError,
        InvalidLinkError,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except TournamentNotFoundError as exc:
        # Raised by _validate_link when linked_tournament_id doesn't
        # match any tournament -- 404 rather than the more common 422
        # here since it's the referenced id, not a field value, that's
        # invalid.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/club/{club_id}")
def list_tournaments_for_club_route(club_id: str):
    return list_tournaments_for_club(club_id)


# Three path segments (club/{club_id}/history) vs. /club/{club_id}'s two,
# so this can't collide with it regardless of registration order -- kept
# next to it anyway since it's the same club-scoped shape, just for
# finalized tournaments. Powers club.py's new History tab.
@router.get("/club/{club_id}/history")
def get_club_tournament_history_route(club_id: str):
    return get_club_tournament_history(club_id)


# NOTE: this must stay registered BEFORE /{tournament_id} below -- same
# routing-order reasoning as the /tee-times/assignments vs /tee-times/
# {tee_time_id} comment further down. Without this, a request to
# /tournaments/scheduled/<player_id> would match /{tournament_id} first,
# treating the literal word "scheduled" as a tournament id.
@router.get("/scheduled/{player_id}")
def list_scheduled_tee_times_route(player_id: str):
    """Powers the Play page's Scheduled tab -- every upcoming tee time
    this player is grouped into, across every tournament in every club
    they belong to. See list_scheduled_tee_times_for_player's own
    docstring for exactly what "upcoming" excludes."""
    return list_scheduled_tee_times_for_player(player_id)


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
    except (
        InvalidFormatError,
        InvalidEntryModeError,
        InvalidGroupingMethodError,
        InvalidHandicapAllowanceError,
        InvalidPairsScoringStyleError,
        NoRoundsError,
        InvalidLinkError,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/{tournament_id}/finalize")
def finalize_tournament_route(tournament_id: str, payload: TournamentFinalizeRequest):
    try:
        return finalize_tournament(tournament_id, payload)
    except TournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (TournamentAlreadyFinalizedError, TournamentNotReadyToFinalizeError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/{tournament_id}/winner")
def get_tournament_winner_route(tournament_id: str):
    winner = get_tournament_winner(tournament_id)
    if not winner:
        # Same 404 whether the tournament doesn't exist at all or just
        # isn't finalized yet -- see get_tournament_winner's own
        # docstring on why that distinction doesn't matter here.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not finalized")
    return winner


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
        return enter_tournament(tournament_id, str(payload.player_id), payload.handicap_source)
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


@router.patch("/{tournament_id}/entrants/{player_id}/handicap-override")
def set_entrant_handicap_override_route(
    tournament_id: str, player_id: str, admin_id: str, payload: TournamentEntrantHandicapOverrideUpdate
):
    try:
        updated = set_entrant_handicap_override(tournament_id, player_id, admin_id, payload.handicap_override)
    except EntrantTournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except EntrantNotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except InvalidHandicapOverrideError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entrant not found")
    return updated

@router.get("/{tournament_id}/pairs")
def list_tournament_pairs_route(tournament_id: str):
    return list_tournament_pairs(tournament_id)


@router.put("/{tournament_id}/pairs")
def set_tournament_pairs_route(tournament_id: str, payload: TournamentPairsSetRequest):
    try:
        return set_tournament_pairs(
            tournament_id, str(payload.admin_id), [[str(p) for p in pair] for pair in payload.pairs]
        )
    except PairsTournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PairsNotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except InvalidPairingError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/{tournament_id}/pairs-leaderboard")
def get_tournament_pairs_leaderboard_route(tournament_id: str, round_id: str):
    # round_id required, same reasoning as get_tournament_leaderboard_route
    # above -- the frontend always has one before calling this.
    try:
        return get_tournament_pairs_leaderboard(tournament_id, round_id)
    except TournamentRoundNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


# One extra path segment vs. the round-scoped route just above, so this
# can't collide with it regardless of registration order (same reasoning
# as /club/{club_id}/history vs. /club/{club_id}). This is the pairs
# equivalent of individual tournaments' "Overall" tab -- unlike that one
# (which just resolves client-side to the latest round's own already-
# cumulative response, see _default_leaderboard_round/task #100), pairs
# rounds carry no cross-round running total of their own, so Overall here
# genuinely needs its own aggregation (get_tournament_pairs_overall_
# leaderboard) rather than being a relabeled round fetch.
@router.get("/{tournament_id}/pairs-leaderboard/overall")
def get_tournament_pairs_overall_leaderboard_route(tournament_id: str):
    try:
        return get_tournament_pairs_overall_leaderboard(tournament_id)
    except TournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))