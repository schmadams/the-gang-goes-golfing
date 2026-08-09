# target path: frontend/src/pages/home.py (full replacement)
import dash
import dash_bootstrap_components as dbc
import requests
from dash import Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/", name="Home")


def layout():
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        # Not signed in (or a stale/incomplete session) — bounce to sign-in
        session.clear()
        return dcc.Location(pathname="/signin", id="redirect-to-signin", refresh=True)

    groups_resp = requests.get(f"{API_BASE_URL}/group-players/player/{player_id}")
    groups = (
        [row["groups"] for row in groups_resp.json()]
        if groups_resp.status_code == 200
        else []
    )

    if groups:
        groups_section = html.Div(
            className="t3g-groups-list",
            children=[
                # NOTE: /groups/<slug> doesn't exist yet — placeholder link
                # for the group home page we haven't built.
                dcc.Link(
                    group["name"],
                    href=f"/groups/{group['slug']}",
                    className="t3g-group-item",
                )
                for group in groups
            ],
        )
    else:
        groups_section = html.P(
            "You're not in any groups yet.", className="t3g-empty-state"
        )

    return html.Div(
        className="t3g-page",
        children=[
            html.Div(
                className="t3g-panel-grid",
                children=[
                    html.Div(
                        className="t3g-panel",
                        children=[
                            build_panel_navbar(
                                "Your Groups",
                                action=[
                                    html.Button(
                                        "Join Group",
                                        id="join-group-button",
                                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                                    ),
                                    html.Button(
                                        "Create Group",
                                        id="create-group-button",
                                        className="t3g-panel-action-button",
                                    ),
                                ],
                            ),
                            html.Div(groups_section, className="t3g-panel-body"),
                        ],
                    ),
                    html.Div(
                        className="t3g-panel",
                        children=[
                            build_panel_navbar(
                                "Rounds History",
                                # NOTE: not wired up yet — no rounds/scores
                                # table exists in the database. Purely
                                # visual until that's designed and built.
                                action=html.Button(
                                    "Upload New Round",
                                    id="upload-round-button",
                                    className="t3g-panel-action-button",
                                ),
                            ),
                            html.Div(
                                html.P(
                                    "No rounds recorded yet.",
                                    className="t3g-empty-state",
                                ),
                                className="t3g-panel-body",
                            ),
                        ],
                    ),
                ],
            ),
            dbc.Modal(
                id="join-group-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Join a Group")),
                    dbc.ModalBody(
                        [
                            dbc.Input(
                                id="join-group-uuid-input",
                                placeholder="Group ID (UUID)",
                                type="text",
                            ),
                            html.Div(
                                id="join-group-error", className="text-danger mt-2"
                            ),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button(
                                "Cancel", id="join-group-cancel", color="secondary"
                            ),
                            dbc.Button("Join", id="join-group-submit", color="primary"),
                        ]
                    ),
                ],
            ),
            dcc.Location(id="join-group-redirect", refresh=True),
            dbc.Modal(
                id="create-group-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Create a Group")),
                    dbc.ModalBody(
                        [
                            dbc.Input(
                                id="create-group-name-input",
                                placeholder="Group name",
                                type="text",
                                className="mb-2",
                            ),
                            dbc.Textarea(
                                id="create-group-description-input",
                                placeholder="Description (optional)",
                            ),
                            html.Div(
                                id="create-group-error", className="text-danger mt-2"
                            ),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button(
                                "Cancel", id="create-group-cancel", color="secondary"
                            ),
                            dbc.Button(
                                "Create", id="create-group-submit", color="primary"
                            ),
                        ]
                    ),
                ],
            ),
            dcc.Location(id="create-group-redirect", refresh=True),
        ],
    )


@callback(
    Output("join-group-modal", "is_open"),
    Output("join-group-error", "children"),
    Output("join-group-redirect", "pathname"),
    Input("join-group-button", "n_clicks"),
    Input("join-group-cancel", "n_clicks"),
    Input("join-group-submit", "n_clicks"),
    State("join-group-uuid-input", "value"),
    prevent_initial_call=True,
)
def handle_join_group(open_clicks, cancel_clicks, submit_clicks, group_uuid):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "join-group-button":
        return True, "", dash.no_update

    if triggered_id == "join-group-cancel":
        return False, "", dash.no_update

    if triggered_id == "join-group-submit":
        if not group_uuid:
            return True, "Enter a group ID.", dash.no_update

        player_id = session.get("player_id")
        response = requests.post(
            f"{API_BASE_URL}/group-players/",
            json={"group_id": group_uuid, "player_id": player_id},
        )

        if response.status_code == 201:
            return False, "", "/"

        if response.status_code == 422:
            return True, "That doesn't look like a valid group ID.", dash.no_update

        # NOTE: the backend doesn't yet distinguish "group not found" from
        # "already a member" — both currently surface as a generic error.
        return (
            True,
            "Couldn't join that group. Check the ID, or you may already be a member.",
            dash.no_update,
        )

    return dash.no_update, dash.no_update, dash.no_update


@callback(
    Output("create-group-modal", "is_open"),
    Output("create-group-error", "children"),
    Output("create-group-redirect", "pathname"),
    Input("create-group-button", "n_clicks"),
    Input("create-group-cancel", "n_clicks"),
    Input("create-group-submit", "n_clicks"),
    State("create-group-name-input", "value"),
    State("create-group-description-input", "value"),
    prevent_initial_call=True,
)
def handle_create_group(open_clicks, cancel_clicks, submit_clicks, name, description):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "create-group-button":
        return True, "", dash.no_update

    if triggered_id == "create-group-cancel":
        return False, "", dash.no_update

    if triggered_id == "create-group-submit":
        if not name:
            return True, "Enter a group name.", dash.no_update

        player_id = session.get("player_id")
        group_resp = requests.post(
            f"{API_BASE_URL}/groups/",
            json={
                "name": name,
                "description": description or None,
                "admin_player_id": player_id,
            },
        )

        if group_resp.status_code == 409:
            return (
                True,
                "A group with a similar name already exists. Try a different name.",
                dash.no_update,
            )
        if group_resp.status_code != 201:
            return True, "Couldn't create the group. Try again.", dash.no_update

        new_group = group_resp.json()

        # Automatically add the creator as a member too, so the group shows
        # up in their own groups list right away. Best-effort: if this call
        # fails, the group still exists and can be joined manually by ID.
        requests.post(
            f"{API_BASE_URL}/group-players/",
            json={"group_id": new_group["id"], "player_id": player_id},
        )

        return False, "", "/"

    return dash.no_update, dash.no_update, dash.no_update