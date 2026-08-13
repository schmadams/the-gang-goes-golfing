# target path: frontend/src/layouts/subnav.py (new file)
import requests
from dash import Input, Output, callback, html
from flask import session

from config import API_BASE_URL


def build_subnav():
    player_id = session.get("player_id")

    handicap = None
    if player_id:
        response = requests.get(f"{API_BASE_URL}/handicaps/player/{player_id}/current")
        if response.status_code == 200:
            handicap = response.json().get("handicap")

    return html.Div(
        className="t3g-subnav",
        children=html.Div(
            className="t3g-subnav-inner",
            children=[
                html.Span(
                    f"Welcome, {session.get('name')}",
                    className="t3g-subnav-greeting",
                ),
                html.Div(
                    className="t3g-subnav-handicap",
                    children=[
                        html.Span("Current Handicap", className="t3g-subnav-label"),
                        html.Span(
                            f"{handicap}" if handicap is not None else "Not set",
                            className="t3g-subnav-handicap-value",
                        ),
                    ],
                ),
            ],
        ),
    )


@callback(
    Output("subnav-container", "children"),
    Input("_pages_location", "pathname"),
)
def render_subnav(pathname):
    # Fires on every navigation (client-side page changes included) since
    # it's keyed off Dash Pages' own pathname tracker, not a one-time
    # server render -- this is what actually makes the subnav disappear
    # when you leave "/" and reappear when you come back, rather than
    # just reflecting whatever page happened to be loaded first.
    if not session.get("logged_in") or pathname != "/":
        return None
    return build_subnav()