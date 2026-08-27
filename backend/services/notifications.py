# target path: backend/services/notifications.py (new file)
from datetime import datetime, timezone

from backend.database import supabase

# The only four categories this app currently creates notifications for --
# each one maps to exactly one nav destination on the frontend (navbar.py's
# bell badge is the sum of all four; bottom_nav.py's Home/Clubs/Play/
# Account tabs each show one category's own count). A category not in
# this tuple can't ever surface an unread badge anywhere, so create_
# notification rejects it outright rather than silently writing a row
# nothing will ever count.
CATEGORIES = ("friends", "play", "clubs", "home")


def create_notification(
    player_id: str,
    category: str,
    title: str,
    body: str | None = None,
    url: str | None = None,
) -> dict:
    """Insert one notification for one recipient. Every call site (see
    friends.py/rounds.py/club_posts.py/round_posts.py/tournament_tee_
    times.py) wraps this in a try/except, same "best-effort side-effect"
    convention as the automated feed-post hooks in club_posts.py -- a
    notification failing to write should never be able to block the
    actual action (a friend request sending, a round finishing, tee
    times publishing) that triggered it.

    A broadcast to several people (e.g. every confirmed tournament
    entrant, or every other member of a round) isn't a single row with
    several recipients -- there's no such thing here, same as club_posts'
    own scorecard posts aren't one row per club either. Call sites that
    need to notify more than one player just call this once per
    recipient."""
    if category not in CATEGORIES:
        raise ValueError(f"Unknown notification category: {category!r} (expected one of {CATEGORIES})")

    response = (
        supabase
        .table("notifications")
        .insert({
            "player_id": player_id,
            "category": category,
            "title": title,
            "body": body,
            "url": url,
        })
        .execute()
    )
    return response.data[0]


def list_notifications(player_id: str, limit: int = 50) -> list[dict]:
    """Newest-first, every category mixed together -- the /notifications
    page's own subnav-free single list, not one tab per category. Read/
    unread state is left exactly as stored (read_at set or not) rather
    than being mutated here -- see mark_all_read below, which the page
    calls separately once it's actually rendered this snapshot, so the
    page can still show what *was* unread a moment ago even though the
    badge counts have already cleared by the time this response reaches
    the browser."""
    response = (
        supabase
        .table("notifications")
        .select("*")
        .eq("player_id", player_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


def count_unread_by_category(player_id: str) -> dict[str, int]:
    """Powers every badge in the app at once -- navbar.py sums this
    dict's values for the single bell badge, bottom_nav.py reads each
    category key individually for its own four tabs. Always returns all
    four keys (zeroed if nothing's unread) rather than only the
    categories that happen to have rows, so callers never need a
    .get(cat, 0) of their own."""
    response = (
        supabase
        .table("notifications")
        .select("category")
        .eq("player_id", player_id)
        .is_("read_at", "null")
        .execute()
    )
    counts = {category: 0 for category in CATEGORIES}
    for row in (response.data or []):
        category = row.get("category")
        if category in counts:
            counts[category] += 1
    return counts


def mark_all_read(player_id: str) -> None:
    """Called once by the /notifications page right after it's fetched
    and rendered the current list -- clears every badge for this player
    in one shot rather than needing a per-row "mark read" click, since
    the whole point of visiting this page is "I've now seen what's
    new"."""
    now = datetime.now(timezone.utc).isoformat()
    (
        supabase
        .table("notifications")
        .update({"read_at": now})
        .eq("player_id", player_id)
        .is_("read_at", "null")
        .execute()
    )