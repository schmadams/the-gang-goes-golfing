# target path: frontend/src/layouts/bottom_nav.py (full replacement)
"""
Instagram-style bottom tab bar for the mobile layout. Fixed to the bottom
of the viewport, hidden entirely above the phone-width breakpoint (see
.t3g-bottom-nav in assets/bottom_nav.css) -- the existing top navbar's own
link row (layouts/navbar.py) hides at that same breakpoint so the two
never show at once, and this takes over as the primary way to move around
the app on a phone.

Five items only, on purpose: Scoring History and Analysis are folded into
one "Analysis" destination (both pathnames light the same tab up as
active, see the prefixes tuples below) rather than each getting their own
icon, and My Account / Courses / Friends stay reachable through the
desktop nav / My Account page rather than being duplicated here. This
mirrors the "which five things does someone actually tap on their phone"
idea behind Instagram's own bottom bar rather than trying to fit the whole
desktop nav into five slots. Notifications doesn't get a sixth slot for
the same reason -- it's reachable from the bell in the top bar instead
(see navbar.py's notifications-indicator, which stays visible on mobile
unlike its other indicators), and its unread counts still surface here
anyway, as small per-tab badges (see below) rather than a destination of
their own.

Active-tab highlighting works the same way layouts/subnav.py keeps itself
in sync with navigation -- keyed off Dash Pages' own pathname tracker
(_pages_location), which fires on client-side navigation too (dcc.Link
clicks), not just hard page loads. Rather than rebuilding the whole nav on
every navigation (subnav's approach), this only patches each link's
className via a pattern-matched Output, since the nav's own structure
never changes -- only which item is "active" does.
"""
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL

# (label, icon, href, active-path prefixes, notification category). A
# pathname "is" this tab if it equals, or falls under, one of its
# prefixes -- e.g. "/clubs/some-slug" (a specific club's page) still
# lights up the Clubs tab, and both "/analysis" and the old
# "/scoring-history" redirect light up Analysis, per the user's call that
# those two should be treated as one destination on mobile.
#
# The notification category is one of backend/services/notifications.py's
# own CATEGORIES (or None for a tab that never gets a badge) -- see
# refresh_bottom_nav_badges below, which reads GET /notifications/
# {player_id}/unread-counts once and hands each tab its own count by
# this same key.
_BOTTOM_NAV_ITEMS = [
    ("Home", "fa-house", "/", ("/",), "home"),
    ("Clubs", "fa-flag", "/clubs", ("/clubs",), "clubs"),
    ("Analysis", "fa-chart-line", "/analysis", ("/analysis", "/scoring-history"), None),
    # Renamed from "Live" -- the page itself is now a hub covering starting
    # a round, live rounds (casual and tournament), and scheduled
    # tournament tee times, not just an in-progress scorecard. See
    # pages/play.py's own module docstring. "/live-round" is kept as a
    # prefix here defensively (any stale link/bookmark still using the old
    # path keeps this tab highlighted) even though nothing in the app links
    # there anymore as of this change.
    ("Play", "fa-golf-ball-tee", "/play", ("/play", "/live-round"), "play"),
    # Friends now lives under the Account umbrella (reachable via the
    # tournament-style subnav at the top of both pages -- see
    # pages/my_account.py's/friends.py's own _account_subnav) rather than
    # getting a bottom-nav slot of its own, so this tab stays highlighted
    # on either page, not just /my-account. Its badge is the "friends"
    # category for the same reason -- a new friend request is the one
    # thing under this umbrella that gets a notification today.
    ("Account", "fa-user", "/my-account", ("/my-account", "/friends"), "friends"),
]


def _is_active(pathname, prefixes):
    pathname = pathname or "/"
    for prefix in prefixes:
        if prefix == "/":
            # "/" is technically a prefix of every path -- only an exact
            # match should count here, otherwise Home would light up on
            # every single page rather than just the home page.
            if pathname == "/":
                return True
        elif pathname == prefix or pathname.startswith(prefix + "/"):
            return True
    return False


def _nav_item_class(active):
    return (
        "t3g-bottom-nav-item t3g-bottom-nav-item--active"
        if active
        else "t3g-bottom-nav-item"
    )


def build_bottom_nav():
    return html.Nav(
        className="t3g-bottom-nav",
        id="bottom-nav",
        children=[
            dcc.Link(
                [
                    html.Div(
                        className="t3g-bottom-nav-icon-wrap",
                        children=[
                            html.I(className=f"fa-solid {icon} t3g-bottom-nav-icon"),
                            # Only tabs with a real category get a badge
                            # wrapper at all -- Analysis' is permanently
                            # empty rather than rendering an id nothing
                            # will ever target.
                            html.Span(id={"type": "bottom-nav-badge", "category": category}, className="t3g-bottom-nav-badge")
                            if category
                            else None,
                        ],
                    ),
                    html.Span(label, className="t3g-bottom-nav-label"),
                ],
                href=href,
                # The prefixes tuple is baked into the id itself (pipe-
                # joined, since dash id values must be JSON-serializable
                # scalars/simple types) so the highlight callback below can
                # decide each link's active state purely from the ids it's
                # matched against, without needing a second State lookup
                # that has to stay in sync with this list by position.
                id={"type": "bottom-nav-link", "prefixes": "|".join(prefixes)},
                className=_nav_item_class(False),
            )
            for label, icon, href, prefixes, category in _BOTTOM_NAV_ITEMS
        ]
        + [dcc.Interval(id="bottom-nav-notif-poll", interval=10_000, n_intervals=0)],
    )


@callback(
    Output({"type": "bottom-nav-link", "prefixes": ALL}, "className"),
    Input("_pages_location", "pathname"),
    State({"type": "bottom-nav-link", "prefixes": ALL}, "id"),
)
def highlight_active_bottom_nav_tab(pathname, ids):
    return [
        _nav_item_class(_is_active(pathname, id_["prefixes"].split("|")))
        for id_ in ids
    ]


@callback(
    Output({"type": "bottom-nav-badge", "category": ALL}, "children"),
    Input("bottom-nav-notif-poll", "n_intervals"),
    State({"type": "bottom-nav-badge", "category": ALL}, "id"),
)
def refresh_bottom_nav_badges(n_intervals, ids):
    """Same shared-poll-interval idea as navbar.py's own indicators, just
    scoped to this bar and running on its own 10s Interval instead of
    reusing navbar's -- the two layouts don't share component ids across
    files, and only one of them is ever actually visible at a given
    breakpoint anyway, so there's no real benefit to threading one
    interval between them."""
    player_id = session.get("player_id")
    if not player_id:
        return ["" for _ in ids]

    response = requests.get(f"{API_BASE_URL}/notifications/{player_id}/unread-counts")
    counts = response.json() if response.status_code == 200 else {}
    if not isinstance(counts, dict):
        counts = {}

    result = []
    for id_ in ids:
        count = counts.get(id_["category"], 0)
        result.append(str(count) if count and count <= 9 else ("9+" if count > 9 else ""))
    return result