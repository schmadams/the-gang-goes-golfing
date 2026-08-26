# target path: backend/routers/rounds.py (full replacement)
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from backend.models.round import (
    HoleScoreUpdate,
    RoundAnalysisPoint,
    RoundDetailResponse,
    RoundInviteResponse,
    RoundStartRequest,
    RoundSummaryResponse,
    TournamentRoundStartRequest,
)
from backend.services.round_posts import (
    NotRoundPostPlayerError,
    RoundPostNotFoundError,
    add_round_post_photo,
)
from backend.services.storage import ImageUploadError
from backend.services.rounds import (
    CannotLeaveRoundError,
    CannotMarkNoResultError,
    ManualScorecardValidationError,
    NotFriendsError,
    NotInGroupingError,
    NotRoundCreatorError,
    NotRoundMemberError,
    RoundAlreadyActiveError,
    RoundInviteNotFoundError,
    RoundNotEditableError,
    RoundNotInProgressError,
    RoundNotPendingSignoffError,
    TooManyInvitesError,
    TournamentTeeTimeNotFoundError,
    delete_round,
    finish_round,
    get_active_round,
    get_player_analysis,
    get_player_distance_profile,
    get_player_scoring_history,
    get_player_scoring_profile,
    get_round,
    leave_round,
    list_pending_round_invites,
    list_pending_signoff_rounds,
    list_player_rounds,
    mark_round_no_result,
    reject_round_signoff,
    respond_to_round_invite,
    sign_off_round,
    start_round,
    start_tournament_round,
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


@router.post("/tournament/{tee_time_id}", response_model=RoundDetailResponse)
def start_tournament_round_route(tee_time_id: str, payload: TournamentRoundStartRequest):
    # Starts, or joins if a groupmate already started it, the shared live
    # round for this tee time grouping -- see start_tournament_round's
    # docstring. Plain 200 (not 201) since "already existed, here it is"
    # is just as valid a result as "just created it".
    try:
        return start_tournament_round(tee_time_id, payload.player_id)
    except TournamentTeeTimeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotInGroupingError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except RoundAlreadyActiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


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


@router.get("/player/{player_id}/scoring-profile")
def get_player_scoring_profile_route(player_id: str):
    """Score-to-par by hole length (par 3/4/5) and average birdies/pars/
    bogeys/double-bogeys-plus per round -- powers the two extra Player
    Analysis charts (see get_player_scoring_profile's docstring)."""
    return get_player_scoring_profile(player_id)


@router.get("/player/{player_id}/distance-profile")
def get_player_distance_profile_route(player_id: str):
    """Average shots taken per hole-distance bin -- powers the Player
    Analysis page's "Avg Shots by Hole Distance" chart (see
    get_player_distance_profile's docstring)."""
    return get_player_distance_profile(player_id)


@router.get("/player/{player_id}/scoring-history")
def get_player_scoring_history_route(player_id: str):
    """Chronological round-by-round total strokes, with validated/
    tournament flags -- powers the Player Analysis page's Scoring History
    chart and its Validated/Tournament/All tabs (see
    get_player_scoring_history's docstring)."""
    return get_player_scoring_history(player_id)


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


# Pending-signoff listing is registered before the plain "/{round_id}"
# route below it in this file's source order -- FastAPI (like Dash Pages,
# see frontend/src/app.py's page_registry sort) matches routes in
# registration order and stops at the first match, and "/{round_id}"'s
# single path segment would otherwise happily swallow "pending-signoff"
# as if it were a round id before this route ever got a chance to match.
@router.get("/pending-signoff/{player_id}", response_model=list[RoundDetailResponse])
def list_pending_signoff_rounds_route(player_id: str):
    return list_pending_signoff_rounds(player_id)


@router.patch("/{round_id}/players/{player_id}/holes/{hole_number}", response_model=RoundDetailResponse)
def update_hole_score_route(round_id: str, player_id: str, hole_number: int, payload: HoleScoreUpdate, updated_by: str):
    # updated_by is a plain query param (not part of HoleScoreUpdate's
    # body) -- it's who's *making* this request, not a score field, and
    # keeping it out of the body means payload.model_dump(exclude_unset=
    # True) still only ever contains real score fields. Required (no
    # default) rather than optional, since "anyone can PATCH anyone's
    # score" is exactly the gap this closes -- see update_hole_score's
    # docstring / NotRoundMemberError.
    try:
        round_ = update_hole_score(round_id, player_id, hole_number, payload.model_dump(exclude_unset=True), updated_by)
    except NotRoundMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except RoundNotEditableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if not round_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round or hole not found")
    return round_


@router.post("/{round_id}/finish", response_model=RoundDetailResponse)
def finish_round_route(round_id: str, requesting_player_id: str):
    # requesting_player_id -- who's actually tapping Finish, not just
    # whoever the round_id belongs to -- is what lets finish_round check
    # they're really an accepted player in the round rather than trusting
    # anyone who knows the id. Required (no default), same reasoning as
    # update_hole_score_route's updated_by.
    try:
        round_ = finish_round(round_id, requesting_player_id)
    except ManualScorecardValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except NotRoundMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not round_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round not found")
    return round_


@router.post("/{round_id}/players/{player_id}/signoff", response_model=RoundDetailResponse)
def sign_off_round_route(round_id: str, player_id: str):
    # Approves this round's final scorecard on this player's behalf. Once
    # every accepted player's signed off, the round itself flips to
    # completed and every player's Handicap Index is recalculated for the
    # first time from it -- see sign_off_round's docstring.
    try:
        return sign_off_round(round_id, player_id)
    except RoundNotPendingSignoffError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except NotRoundMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


@router.post("/{round_id}/players/{player_id}/reject", response_model=RoundDetailResponse)
def reject_round_signoff_route(round_id: str, player_id: str):
    # Sends the round back for edits -- reopens it to in_progress and
    # clears everyone's sign-off, not just this player's, since the
    # scorecard they approved is about to change. See reject_round_
    # signoff's docstring.
    try:
        return reject_round_signoff(round_id, player_id)
    except RoundNotPendingSignoffError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except NotRoundMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


@router.post("/{round_id}/players/{player_id}/no-result", response_model=RoundDetailResponse)
def mark_round_no_result_route(round_id: str, player_id: str):
    # Bulk-fills this player's own 18 holes with No Return -- see mark_
    # round_no_result's docstring for exactly what this does and doesn't
    # touch (only this player's own card, only a still-in_progress
    # tournament round). Returns the full round detail, same as signoff/
    # reject, so the frontend can refresh straight from the response.
    try:
        mark_round_no_result(round_id, player_id)
    except RoundNotInProgressError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except CannotMarkNoResultError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except NotRoundMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    round_ = get_round(round_id, viewer_player_id=player_id)
    if not round_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round not found")
    return round_


@router.get("/{round_id}", response_model=RoundDetailResponse)
def get_round_route(round_id: str, viewer_player_id: str | None = None):
    # viewer_player_id is optional -- omitting it (the old behavior) just
    # means the response's top-level is_owner comes back null instead of
    # computed. /live-round passes its own session player_id here when
    # loading a round directly by id (the tournament-round case, since
    # there can be more than one live round for a player at once -- see
    # get_active_round_route for the single-active-casual-round lookup).
    round_ = get_round(round_id, viewer_player_id=viewer_player_id)
    if not round_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round not found")
    return round_


@router.delete("/{round_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_round_route(round_id: str, requesting_player_id: str | None = None):
    # Same endpoint serves two UI actions -- "Scrap Round" on an
    # in-progress round (live round page or Scoring History), and
    # "Delete" on an already-finished round from Scoring History. Both
    # are just "this round row (and its scores) shouldn't exist anymore."
    # requesting_player_id is optional -- it's only actually enforced for
    # the Scrap-a-casual-in-progress-round case (see delete_round's
    # docstring); every existing caller now passes its own session
    # player_id regardless, but omitting it just skips that one check
    # rather than erroring, so this stays backward compatible.
    try:
        deleted = delete_round(round_id, requesting_player_id)
    except NotRoundCreatorError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round not found")


@router.post("/{round_id}/players/{player_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_round_route(round_id: str, player_id: str):
    # Lets a non-creator accepted player exit a still-in_progress casual
    # round without scrapping it for whoever's still in it -- see
    # leave_round's docstring for exactly what this does and doesn't
    # allow (the creator, and any tournament round, are both refused).
    try:
        leave_round(round_id, player_id)
    except RoundNotInProgressError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except CannotLeaveRoundError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except NotRoundMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


@router.post("/{round_id}/post/photo", status_code=status.HTTP_201_CREATED)
async def add_round_post_photo_route(
    round_id: str,
    author_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Adds one more photo to a round's post, any time after the fact --
    the round-completion side of the home feed already creates the post
    itself (see finish_round/sign_off_round), this just lets one of its
    players attach a photo later. See add_round_post_photo's own
    docstring in backend/services/round_posts.py for the exact
    membership rule."""
    file_bytes = await file.read()
    try:
        return add_round_post_photo(round_id, author_id, file_bytes, file.filename, file.content_type)
    except RoundPostNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotRoundPostPlayerError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ImageUploadError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))