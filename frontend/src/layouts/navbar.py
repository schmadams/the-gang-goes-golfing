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
                        html.Div(id="live-round-indicator"),
                        # Polls rather than reacting to navigation, since
                        # this navbar sits outside dash's page_container and
                        # doesn't re-render on client-side page changes --
                        # this is what lets the indicator appear a few
                        # seconds after a round is started from any page,
                        # and disappear again once it's finished.
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
        href="/live-round",
        className="t3g-live-round-indicator",
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