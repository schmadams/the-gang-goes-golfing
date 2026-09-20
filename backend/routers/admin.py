# target path: backend/routers/admin.py (new file)
"""Thin HTTP wrapping around backend/services/admin_cleanup.py -- see that
module's own docstring for the actual cascade logic and the dry_run
safety model. No auth/admin-role check here at all (this app has none
yet) -- these routes are reachable by anyone who can reach the backend.
Fine for a private trip app you alone operate against a private Railway
URL; add a real permission check in front of this router specifically
before relying on that continuing to be true.

Every route defaults query param dry_run=True to match the underlying
functions' own safe-by-default design -- a caller has to explicitly pass
?dry_run=false to actually delete anything. tasks.py's invoke commands
are the intended way to drive these day to day (see those for the exact
two-step "preview, then confirm" flow); hitting these routes directly
with a tool like curl/Postman works the same way.
"""
from fastapi import APIRouter, HTTPException, Query, status

from backend.services.admin_cleanup import (
    ClubNotFoundError,
    PlayerAccountNotFoundError,
    delete_club_cascade,
    delete_player_account_cascade,
    diagnose_tournament_rounds,
    list_player_accounts,
    reset_clubs_keep_one,
    wipe_all_tournaments,
)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
)


@router.get("/player-accounts")
def list_player_accounts_route():
    return list_player_accounts()


@router.post("/tournaments/wipe")
def wipe_all_tournaments_route(dry_run: bool = Query(default=True)):
    return wipe_all_tournaments(dry_run=dry_run)


@router.post("/clubs/reset")
def reset_clubs_route(
    keep_slug: str | None = Query(default=None),
    delete_all: bool = Query(default=False),
    dry_run: bool = Query(default=True),
):
    # Deliberately requires an explicit delete_all=true to take the
    # "keep nothing" path -- a caller who simply forgets keep_slug (or
    # typos it) gets a 400 telling them so, rather than silently wiping
    # every club because keep_slug came through as empty/None.
    if keep_slug is None and not delete_all:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide keep_slug, or pass delete_all=true to delete every club.",
        )
    try:
        return reset_clubs_keep_one(keep_slug, dry_run=dry_run)
    except ClubNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.delete("/clubs/{club_slug}")
def delete_club_route(club_slug: str, dry_run: bool = Query(default=True)):
    try:
        return delete_club_cascade(club_slug, dry_run=dry_run)
    except ClubNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.delete("/player-accounts/{player_account_id}")
def delete_player_account_route(player_account_id: str, dry_run: bool = Query(default=True)):
    try:
        return delete_player_account_cascade(player_account_id, dry_run=dry_run)
    except PlayerAccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/clubs/{club_slug}/diagnose-rounds")
def diagnose_tournament_rounds_route(club_slug: str):
    # Read-only -- no dry_run param, this can never mutate anything.
    try:
        return diagnose_tournament_rounds(club_slug)
    except ClubNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))