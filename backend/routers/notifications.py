# target path: backend/routers/notifications.py (new file)
from fastapi import APIRouter

from backend.services.notifications import (
    count_unread_by_category,
    list_notifications,
    mark_all_read,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/{player_id}")
def list_notifications_route(player_id: str, limit: int = 50):
    """Backs the /notifications page's own list -- newest-first, every
    category mixed together. See list_notifications' docstring in
    backend/services/notifications.py for why marking these read is a
    separate call (mark_all_read_route below) rather than something this
    route does itself."""
    return list_notifications(player_id, limit=limit)


@router.get("/{player_id}/unread-counts")
def unread_counts_route(player_id: str):
    """Polled by both navbar.py (summed into one bell badge) and
    bottom_nav.py (one badge per tab) -- same shared-poll-interval
    pattern navbar.py's own live-round/sign-off indicators already use,
    just with a dict of counts instead of a single boolean/list."""
    return count_unread_by_category(player_id)


@router.post("/{player_id}/read-all")
def mark_all_read_route(player_id: str):
    mark_all_read(player_id)
    return {"status": "ok"}