# target path: backend/routers/players.py (full replacement)
from fastapi import APIRouter, File, HTTPException, UploadFile, status

from backend.models.player import PlayerCreate, PlayerResponse, PlayerUpdate
from backend.services.players import (
    NotFriendsWithPlayerError,
    create_player,
    get_player,
    get_player_profile,
    list_players,
    update_player,
    upload_profile_picture,
)
from backend.services.round_posts import list_home_feed_posts
from backend.services.storage import ImageUploadError

router = APIRouter(
    prefix="/players",
    tags=["players"],
)


@router.get("/", response_model=list[PlayerResponse])
def list_players_route():
    return list_players()


@router.post("/", response_model=PlayerResponse, status_code=status.HTTP_201_CREATED)
def create_player_route(player: PlayerCreate):
    return create_player(
        player.first_name,
        player.surname,
        player.date_of_birth.isoformat() if player.date_of_birth else None,
    )


@router.get("/{player_id}", response_model=PlayerResponse)
def get_player_route(player_id: str):
    player = get_player(player_id)

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Player not found"
        )

    return player


@router.get("/{player_id}/profile")
def get_player_profile_route(player_id: str, viewer_player_id: str):
    # viewer_player_id is required (no default) -- unlike get_player_route
    # above, this endpoint is deliberately gated: get_player_profile
    # raises NotFriendsWithPlayerError for anyone who isn't player_id's
    # confirmed friend (or player_id themselves), see its docstring in
    # backend/services/players.py. Powers the friends-visible player
    # profile page.
    try:
        profile = get_player_profile(player_id, viewer_player_id)
    except NotFriendsWithPlayerError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")

    return profile


@router.patch("/{player_id}", response_model=PlayerResponse)
def update_player_route(player_id: str, player: PlayerUpdate):
    payload = player.model_dump(exclude_unset=True)

    if payload.get("date_of_birth") is not None:
        payload["date_of_birth"] = payload["date_of_birth"].isoformat()

    updated = update_player(player_id, payload)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Player not found"
        )

    return updated


@router.post("/{player_id}/profile-picture", response_model=PlayerResponse)
async def upload_profile_picture_route(player_id: str, file: UploadFile = File(...)):
    file_bytes = await file.read()
    try:
        updated = upload_profile_picture(player_id, file_bytes, file.filename, file.content_type)
    except ImageUploadError as exc:
        # 502 (not 500) -- this app's own code ran fine, it's the upstream
        # Supabase Storage call that failed (see ImageUploadError's
        # docstring, usually a missing/non-public bucket). str(exc) is
        # deliberately in the response body here rather than swallowed,
        # so whoever's debugging this doesn't need backend log access to
        # see it.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Player not found"
        )

    return updated


@router.get("/{player_id}/feed")
def get_player_feed_route(player_id: str):
    """Home feed for one player -- every round they or a friend played
    (with their own detailed scorecard/handicap change folded in for
    their own rounds), plus every post from every club they belong to.
    See list_home_feed_posts's own docstring in backend/services/
    round_posts.py for exactly how those two sources are merged and
    de-duplicated."""
    return list_home_feed_posts(player_id)