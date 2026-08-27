# target path: frontend/src/pages/notifications.py (new file)
"""
The page behind the navbar's bell badge (layouts/navbar.py's
notifications-indicator) -- every notification this player's ever gotten,
newest first, all four categories mixed together in one list (no per-
category tabs -- see backend/services/notifications.py's own
list_notifications docstring for why a single feed is enough here).

Visiting this page is what clears every unread badge in the app (the
bell's own count, plus bottom_nav.py's four per-tab counts) -- see
mark_all_read below, fired once right after the current list has already
been fetched and rendered, so a row that *was* unread a second ago still
shows that way on this exact render even though the underlying badge
counts have already cleared by the time this page finishes loading. The
next visit (or the next poll tick elsewhere in the app) is what actually
makes the badges disappear.
"""
from datetime import datetime

import dash
import requests
from dash import dcc, html
from flask import session

from config import API_BASE_URL

dash.register_page(__name__, path="/notifications", name="Notifications")


def _format_timestamp(iso_str):
    """"D Mon YYYY, HH:MM" -- same helper as home.py's own
    _format_feed_timestamp, copied rather than imported (small per-page
    duplicated helper, same convention as every other page-local
    formatter in this app) -- see that one's docstring for why the day-
    of-month is built by hand instead of via strftime's platform-
    dependent %-d/%#d."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return iso_str
    return f"{dt.day} {dt.strftime('%b %Y, %H:%M')}"


def _notification_row(notification):
    is_unread = notification.get("read_at") is None
    row_class = "t3g-notification-row t3g-notification-row--unread" if is_unread else "t3g-notification-row"

    content = [
        html.Div(
            className="t3g-notification-row-main",
            children=[
                html.Span(className="t3g-notification-dot") if is_unread else None,
                html.Div(
                    className="t3g-notification-row-text",
                    children=[
                        html.Div(notification["title"], className="t3g-notification-title"),
                        html.Div(_format_timestamp(notification.get("created_at")), className="t3g-notification-timestamp"),
                    ],
                ),
            ],
        ),
    ]

    if notification.get("url"):
        return dcc.Link(content, href=notification["url"], className=row_class)
    return html.Div(content, className=row_class)


def layout(**kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="notifications-redirect-signin", refresh=True)

    response = requests.get(f"{API_BASE_URL}/notifications/{player_id}")
    notifications = response.json() if response.status_code == 200 else []

    # Fired right after the fetch above, not before -- this render still
    # reflects whatever was actually unread a moment ago (see this
    # module's own docstring). Best-effort in the sense that a failure
    # here just means the badges take one more visit to clear, not that
    # anything about this page itself breaks.
    if notifications:
        try:
            requests.post(f"{API_BASE_URL}/notifications/{player_id}/read-all")
        except requests.RequestException:
            pass

    body = (
        html.Div([_notification_row(n) for n in notifications], className="t3g-notification-list")
        if notifications
        else html.P(
            "Nothing here yet -- you'll see friend requests, round sign-offs, "
            "feed activity, and tournament updates as they happen.",
            className="t3g-empty-state",
        )
    )

    return html.Div(
        # .t3g-page is the shared site-wide outer wrapper (home.py,
        # friends.py, clubs.py, etc. -- see its own docstring in assets/
        # home.css) rather than a page-local one, same as every other
        # top-level page in this app.
        className="t3g-page",
        children=[
            html.H2("Notifications", className="t3g-notifications-heading"),
            body,
        ],
    )