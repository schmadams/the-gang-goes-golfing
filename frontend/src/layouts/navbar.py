# target path: frontend/src/layouts/navbar.py (full replacement)
import requests
from dash import Input, Output, callback, dcc, html
from flask import session

from config import API_BASE_URL

# NOTE: "Courses" and "Scoring History" don't have real pages behind them
# yet — these links will 404 until those pages exist.


def build_navbar():
    return html.Nav(
        className="t3g-navbar",
        children=html.Div(
            className="t3g-navbar-inner",
            children=[
                html.Div(
                    className="t3g-navbar-left",
                    children=[
                        dcc.Link(
                            [
                                html.Span("T", className="t3g-brand-part"),
                                html.Span("3", className="t3g-brand-accent"),
                                html.Span("G", className="t3g-brand-part"),
                            ],
                            href="/",
                            className="t3g-brand",
                        ),
                        html.Div(
                            className="t3g-nav-links",
                            children=[
                                dcc.Link(
                                    # Play is the new hub for starting a
                                    # round, live rounds (casual and
                                    # tournament), and scheduled tournament
                                    # tee times -- see pages/play.py's own
                                    # module docstring. Previously the only
                                    # way to reach this page from the
                                    # desktop navbar was the conditional
                                    # "Live round in progress" pill on the
                                    # far right; this is now a permanent
                                    # link like the others, always visible.
                                    "Play",
                                    href="/play",
                                    className="t3g-nav-link",
                                ),
                                dcc.Link(
                                    "My Account",
                                    href="/my-account",
                                    className="t3g-nav-link",
                                ),
                                dcc.Link(
                                    "Courses",
                                    href="/courses",
                                    className="t3g-nav-link",
                                ),
                                dcc.Link(
                                    # Scoring History and Analysis are now one merged
                                    # page (pages/analysis.py) behind a tab subnav --
                                    # one "Analysis" link here instead of two.
                                    "Analysis",
                                    href="/analysis",
                                    className="t3g-nav-link",
                                ),
                                dcc.Link(
                                    "Friends",
                                    href="/friends",
                                    className="t3g-nav-link",
                                ),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    className="t3g-navbar-actions",
                    children=[
                        html.Div(id="round-signoff-indicator"),
                        html.Div(id="live-round-indicator"),
                        # Polls rather than reacting to navigation, since
                        # this navbar sits outside dash's page_container and
                        # doesn't re-render on client-side page changes --
                        # this is what lets the indicator(s) appear a few
                        # seconds after a round is started (or finished, for
                        # the sign-off pill) from any page, and disappear
                        # again once it's finished / everything's signed
                        # off. One shared interval drives both indicators
                        # (see refresh_live_round_indicator and
                        # refresh_round_signoff_indicator below) rather than
                        # each polling on its own.
                        dcc.Interval(id="live-round-poll", interval=10_000, n_intervals=0),
                        html.Button(
                            "Sign out",
                            id="signout-button",
                            className="t3g-signout-button",
                        ),
                        dcc.Location(id="signout-redirect", refresh=True),
                    ],
                ),
            ],
        ),
    )


@callback(
    Output("live-round-indicator", "children"),
    Input("live-round-poll", "n_intervals"),
)
def refresh_live_round_indicator(n_intervals):
    player_id = session.get("player_id")
    if not player_id:
        return None

    response = requests.get(f"{API_BASE_URL}/rounds/active/{player_id}")
    if response.status_code != 200:
        return None

    return dcc.Link(
        [
            html.Span(className="t3g-live-dot"),
            html.Span("Live round in progress"),
        ],
        href="/play",
        className="t3g-live-round-indicator",
    )


@callback(
    Output("round-signoff-indicator", "children"),
    Input("live-round-poll", "n_intervals"),
)
def refresh_round_signoff_indicator(n_intervals):
    # Same shared 10s poll as the live-round indicator next to it -- see
    # that callback's comment. Counts rounds this player is an accepted
    # participant in, still pending_signoff, and hasn't signed off on yet
    # (GET /rounds/pending-signoff/{player_id} already excludes anything
    # this player already approved -- see list_pending_signoff_rounds in
    # backend/services/rounds.py), so the pill disappears the moment
    # there's genuinely nothing left for this player to review, not just
    # when every round is fully completed.
    player_id = session.get("player_id")
    if not player_id:
        return None

    response = requests.get(f"{API_BASE_URL}/rounds/pending-signoff/{player_id}")
    if response.status_code != 200:
        return None

    pending_rounds = response.json()
    if not pending_rounds:
        return None

    count = len(pending_rounds)
    label = "1 round needs your sign-off" if count == 1 else f"{count} rounds need your sign-off"

    return dcc.Link(
        [
            html.Span(className="t3g-signoff-dot"),
            html.Span(label),
        ],
        href="/round-signoff",
        className="t3g-signoff-indicator",
    )


@callback(
    Output("signout-redirect", "href"),
    Input("signout-button", "n_clicks"),
    prevent_initial_call=True,
)
def handle_signout(n_clicks):
    # href (not pathname) -- this Location lives in the navbar, outside
    # Dash Pages' own page_container, so it's a separate component from
    # _pages_location with its own "search" prop that just mirrors
    # whatever query string happened to already be in the browser's URL
    # (e.g. a leftover "?_r=..." cache-buster from an Accept/Decline click
    # right before Sign out). Writing only "pathname" leaves that stale
    # search string attached, so the redirect actually lands on
    # "/signin?_r=..." -- and since query params get passed through to a
    # page's layout() as kwargs, signin.py's layout(**kwargs) would
    # normally swallow that fine, but if this same signout-redirect
    # component's target page ever doesn't declare **kwargs, that stale
    # "_r" blows up exactly like this. href sidesteps all of it by
    # navigating to a single, complete, self-contained URL instead of
    # letting Dash merge in whatever "search" was last set to.
    session.clear()
    return "/signin"