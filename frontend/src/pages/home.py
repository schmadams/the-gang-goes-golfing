# target path: frontend/src/pages/home.py (full replacement)
import dash
import requests
from dash import Input, Output, callback, dcc, html
from flask import session

from config import API_BASE_URL

dash.register_page(__name__, path="/", name="Home")


def layout():
    if not session.get("logged_in"):
        # Not signed in — bounce to the sign-in page
        return dcc.Location(pathname="/signin", id="redirect-to-signin")

    player_id = session["player_id"]

    groups_resp = requests.get(f"{API_BASE_URL}/group-players/player/{player_id}")
    groups = (
        [row["groups"] for row in groups_resp.json()]
        if groups_resp.status_code == 200
        else []
    )

    handicap_resp = requests.get(f"{API_BASE_URL}/handicaps/player/{player_id}/current")
    handicap = (
        handicap_resp.json().get("handicap")
        if handicap_resp.status_code == 200
        else None
    )

    if groups:
        groups_list = html.Ul(
            [
                # NOTE: /groups/<slug> doesn't exist yet — this is a placeholder
                # link for the group home page we haven't built.
                html.Li(dcc.Link(group["name"], href=f"/groups/{group['slug']}"))
                for group in groups
            ]
        )
    else:
        groups_list = html.P("You're not in any groups yet.")

    return html.Div(
        [
            html.H2(f"Welcome, {session.get('name')}"),
            html.P(
                f"Current handicap: {handicap}"
                if handicap is not None
                else "Current handicap: not set"
            ),
            html.H4("Your groups", className="mt-4"),
            groups_list,
            html.Button("Sign out", id="signout-button", className="mt-3"),
            dcc.Location(id="signout-redirect"),
        ],
        style={"color": "white", "padding": "2rem"},
    )


@callback(
    Output("signout-redirect", "pathname"),
    Input("signout-button", "n_clicks"),
    prevent_initial_call=True,
)
def handle_signout(n_clicks):
    session.clear()
    return "/signin"