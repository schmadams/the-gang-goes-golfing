# target path: backend/routers/clubs.py (full replacement)
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from backend.models.club import ClubCreate, ClubResponse
from backend.services.club_posts import (
    NotClubMemberError,
    create_manual_post,
    get_club_feed,
)
from backend.services.clubs import (
    ClubNotFoundError,
    DuplicateClubError,
    NotClubAdminError,
    create_club,
    delete_club,
    get_club_by_slug,
    list_clubs,
    upload_club_photo,
)
from backend.services.rounds import get_club_player_comparison
from backend.services.storage import ImageUploadError


router = APIRouter(
    prefix="/clubs",
    tags=["clubs"],
)


@router.get("/", response_model=list[ClubResponse])
def list_clubs_route():
    return list_clubs()


@router.get("/slug/{slug}", response_model=ClubResponse)
def get_club_by_slug_route(slug: str):
    club = get_club_by_slug(slug)

    if not club:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Club not found",
        )

    return club


@router.post("/", response_model=ClubResponse, status_code=status.HTTP_201_CREATED)
def create_club_route(club: ClubCreate):
    try:
        return create_club(club)
    except DuplicateClubError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/{club_id}/player-comparison")
def get_club_player_comparison_route(club_id: str):
    """Player-vs-player analysis for this club's own rounds only (club
    tournament rounds + casual rounds tagged with this club) -- see
    get_club_player_comparison's docstring in backend/services/rounds.py
    for exactly what counts as "this club's rounds"."""
    return get_club_player_comparison(club_id)


@router.post("/{club_id}/photo", response_model=ClubResponse)
async def upload_club_photo_route(club_id: str, admin_id: str = Form(...), file: UploadFile = File(...)):
    # admin_id comes in as a form field alongside the file (not a query
    # param, not inferred from a session) -- multipart/form-data requests
    # can carry both fields and a file in one POST, and the frontend
    # already has the requesting player's id in session at upload time,
    # same shape as how tournament creation passes admin_id in its JSON
    # body for the same "only the admin can do this" check.
    file_bytes = await file.read()
    try:
        updated = upload_club_photo(club_id, admin_id, file_bytes, file.filename, file.content_type)
    except ClubNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ImageUploadError as exc:
        # Same 502 treatment as the player photo upload route -- this
        # app's own code ran fine, it's the upstream Supabase Storage
        # call that failed (see ImageUploadError's docstring).
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")

    return updated


@router.get("/{club_id}/feed")
def get_club_feed_route(club_id: str, limit: int = 30):
    """Newest-first mixed feed of every post type for this club -- see
    get_club_feed's docstring in backend/services/club_posts.py for
    exactly how each post_type gets hydrated for display."""
    return get_club_feed(club_id, limit=limit)


@router.post("/{club_id}/feed", status_code=status.HTTP_201_CREATED)
def create_club_post_route(
    club_id: str,
    author_id: str = Form(...),
    body: str = Form(...),
):
    # Text-only -- a manual post has no round behind it, so it can't
    # carry a photo (only a round post can, via add_round_post_photo on
    # the finished round itself). Still checked server-side even though
    # the frontend composer already requires text before enabling Post.
    if not body.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add some text before posting.",
        )

    try:
        return create_manual_post(club_id, author_id, body.strip())
    except NotClubMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


@router.delete("/{club_id}", response_model=ClubResponse)
def delete_club_route(club_id: str):
    deleted_club = delete_club(club_id)

    if not deleted_club:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Club not found",
        )

    return deleted_club