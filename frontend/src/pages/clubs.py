# target path: frontend/src/pages/clubs.py (new file)
"""
Standalone "Your Clubs" page -- the same club grid and Create Club flow
that's always lived inline on Home (see _club_initials/clubs_section/the
create-club-* callback in pages/home.py), pulled out onto its own route so
the mobile bottom nav's "Clubs" tab (layouts/bottom_nav.py) has a real
destination to point at. Home's own inline clubs panel is untouched --
this doesn't replace it, it just gives Clubs a direct page of its own too.
"""
import dash
import dash_bootstrap_components as dbc
import requests
from dash import Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/clubs", name="Clubs")


def _club_initials(name):
    words = (name or "").split()
    initials = "".join(w[0] for w in words[:2] if w)
    return initials.upper() or "?"


def layout(**kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="clubs-redirect-signin", refresh=True)

    clubs_resp = requests.get(f"{API_BASE_URL}/club-players/player/{player_id}")
    clubs = (
        [row["clubs"] for row in clubs_resp.json()]
        if clubs_resp.status_code == 200
        else []
    )

    if clubs:
        clubs_section = html.Div(
            className="t3g-clubs-list",
            children=[
                dcc.Link(
                    href=f"/clubs/{club['slug']}",
                    className="t3g-club-item",
                    children=[
                        (
                            html.Img(src=club["photo_url"], className="t3g-club-item-photo")
                            if club.get("photo_url")
                            else html.Div(
                                _club_initials(club.get("name")),
                                className="t3g-club-item-photo-placeholder",
                            )
                        ),
                        html.Div(club["name"], className="t3g-club-item-name"),
                    ],
                )
                for club in clubs
            ],
        )
    else:
        clubs_section = html.P(
            "You're not in any clubs yet.", className="t3g-empty-state"
        )

    return html.Div(
        className="t3g-page",
        children=[
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar(
                        "Your Clubs",
                        action=html.Button(
                            "Create Club",
                            id="clubs-create-club-button",
                            className="t3g-panel-action-button",
                        ),
                    ),
                    html.Div(clubs_section, className="t3g-panel-body"),
                ],
            ),
            dbc.Modal(
                id="clubs-create-club-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Create a Club")),
                    dbc.ModalBody(
                        [
                            dbc.Input(
                                id="clubs-create-club-name-input",
                                placeholder="Club name",
                                type="text",
                                className="mb-2",
                            ),
                            dbc.Textarea(
                                id="clubs-create-club-description-input",
                                placeholder="Description (optional)",
                            ),
                            html.Div(
                                id="clubs-create-club-error", className="text-danger mt-2"
                            ),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button(
                                "Cancel", id="clubs-create-club-cancel", color="secondary"
                            ),
                            dbc.Button(
                                "Create", id="clubs-create-club-submit", color="primary"
                            ),
                        ]
                    ),
                ],
            ),
            dcc.Location(id="clubs-create-club-redirect", refresh=True),
        ],
    )


@callback(
    Output("clubs-create-club-modal", "is_open"),
    Output("clubs-create-club-error", "children"),
    Output("clubs-create-club-redirect", "pathname"),
    Input("clubs-create-club-button", "n_clicks"),
    Input("clubs-create-club-cancel", "n_clicks"),
    Input("clubs-create-club-submit", "n_clicks"),
    State("clubs-create-club-name-input", "value"),
    State("clubs-create-club-description-input", "value"),
    prevent_initial_call=True,
)
def handle_create_club(open_clicks, cancel_clicks, submit_clicks, name, description):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "clubs-create-club-button":
        return True, "", dash.no_update

    if triggered_id == "clubs-create-club-cancel":
        return False, "", dash.no_update

    if triggered_id == "clubs-create-club-submit":
        if not name:
            return True, "Enter a club name.", dash.no_update

        player_id = session.get("player_id")
        club_resp = requests.post(
            f"{API_BASE_URL}/clubs/",
            json={
                "name": name,
                "description": description or None,
                "admin_player_id": player_id,
            },
        )

        if club_resp.status_code == 409:
            return (
                True,
                "A club with a similar name already exists. Try a different name.",
                dash.no_update,
            )
        if club_resp.status_code != 201:
            return True, "Couldn't create the club. Try again.", dash.no_update

        new_club = club_resp.json()

        # Best-effort, same as home.py's version of this flow: if this
        # second call fails the club still exists and can be joined
        # manually, it just won't show up in the creator's own list yet.
        requests.post(
            f"{API_BASE_URL}/club-players/",
            json={"club_id": new_club["id"], "player_id": player_id},
        )

        return False, "", "/clubs"

    return dash.no_update, dash.no_update, dash.no_update