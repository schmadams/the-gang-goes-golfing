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
                                    "Scoring History",
                                    href="/scoring-history",
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
    Output("signout-redirect", "pathname"),
    Input("signout-button", "n_clicks"),
    prevent_initial_call=True,
)
def handle_signout(n_clicks):
    session.clear()
    return "/signin"