# target path: frontend/src/layouts/subnav.py (new file)
import requests
from dash import html
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