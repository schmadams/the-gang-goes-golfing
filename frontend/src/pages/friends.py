# target path: frontend/src/pages/friends.py (new file)
import dash
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/friends", name="Friends")


def _player_label(player):
    return player.get("nickname") or f"{player.get('first_name', '')} {player.get('surname', '')}".strip()


def _request_row(request_row, other_player, action_children):
    return html.Div(
        className="t3g-friend-request-row",
        children=[
            html.Span(_player_label(other_player), className="t3g-friend-request-name"),
            html.Div(action_children, className="t3g-friend-request-actions"),
        ],
    )


def layout():
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="friends-redirect-signin", refresh=True)

    players_resp = requests.get(f"{API_BASE_URL}/players/")
    all_players = players_resp.json() if players_resp.status_code == 200 else []

    requests_resp = requests.get(f"{API_BASE_URL}/friends/requests/{player_id}")
    pending = requests_resp.json() if requests_resp.status_code == 200 else {"incoming": [], "outgoing": []}
    incoming = pending.get("incoming", [])
    outgoing = pending.get("outgoing", [])

    friends_resp = requests.get(f"{API_BASE_URL}/friends/player/{player_id}")
    friends = friends_resp.json() if friends_resp.status_code == 200 else []

    # Nobody you're already friends with, already have a pending request
    # with (either direction), or yourself -- sending one of those would
    # just bounce off the backend's duplicate check anyway.
    excluded_ids = {player_id}
    excluded_ids.update(f["player_id"] for f in friends)
    excluded_ids.update(r["requester_id"] for r in incoming)
    excluded_ids.update(r["recipient_id"] for r in outgoing)

    friend_options = [
        {"label": _player_label(p), "value": p["id"]}
        for p in all_players
        if p["id"] not in excluded_ids
    ]

    if incoming:
        requests_section = html.Div(
            className="t3g-friend-request-list",
            children=[
                _request_row(
                    r, r["requester"],
                    [
                        html.Button(
                            "Accept",
                            id={"type": "friend-request-accept", "request_id": r["id"]},
                            className="t3g-panel-action-button",
                            n_clicks=0,
                        ),
                        html.Button(
                            "Decline",
                            id={"type": "friend-request-decline", "request_id": r["id"]},
                            className="t3g-panel-action-button t3g-panel-action-button--secondary",
                            n_clicks=0,
                        ),
                    ],
                )
                for r in incoming
            ],
        )
    else:
        requests_section = html.P("No pending friend requests.", className="t3g-empty-state")

    outgoing_section = (
        html.Div(
            className="t3g-friend-request-list",
            children=[
                _request_row(r, r["recipient"], html.Span("Pending", className="t3g-friend-request-pending"))
                for r in outgoing
            ],
        )
        if outgoing
        else None
    )

    if friends:
        friends_section = html.Div(
            className="t3g-clubs-list",
            children=[html.Div(_player_label(f), className="t3g-club-item") for f in friends],
        )
    else:
        friends_section = html.P("You haven't added any friends yet.", className="t3g-empty-state")

    return html.Div(
        className="t3g-page",
        children=[
            dcc.Location(id="friends-refresh", refresh=True),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("Add a Friend"),
                    html.Div(
                        className="t3g-panel-body",
                        children=[
                            dcc.Dropdown(
                                id="friend-request-player",
                                placeholder="Search for a player",
                                options=friend_options,
                                searchable=True,
                                clearable=True,
                                className="mb-2 t3g-course-dropdown",
                            ),
                            html.Div(id="friend-request-error", className="text-danger mt-2"),
                            html.Button(
                                "Send Request",
                                id="friend-request-send",
                                className="t3g-panel-action-button mt-2",
                                n_clicks=0,
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("Friend Requests"),
                    html.Div(className="t3g-panel-body", children=[requests_section, outgoing_section]),
                ],
            ),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("Your Friends"),
                    html.Div(friends_section, className="t3g-panel-body"),
                ],
            ),
        ],
    )


@callback(
    Output("friend-request-error", "children"),
    Output("friends-refresh", "pathname"),
    Input("friend-request-send", "n_clicks"),
    State("friend-request-player", "value"),
    prevent_initial_call=True,
)
def send_friend_request(n_clicks, recipient_id):
    if not recipient_id:
        return "Pick a player first.", dash.no_update

    player_id = session.get("player_id")
    response = requests.post(
        f"{API_BASE_URL}/friends/requests",
        json={"requester_id": player_id, "recipient_id": recipient_id},
    )

    if response.status_code == 201:
        return "", "/friends"

    try:
        detail = response.json().get("detail", "Couldn't send that friend request.")
    except ValueError:
        detail = "Couldn't send that friend request."
    return detail, dash.no_update


@callback(
    Output("friends-refresh", "pathname", allow_duplicate=True),
    Input({"type": "friend-request-accept", "request_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def accept_friend_request(n_clicks_list):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(n_clicks_list):
        return dash.no_update

    player_id = session.get("player_id")
    requests.post(
        f"{API_BASE_URL}/friends/requests/{triggered_id['request_id']}/accept",
        params={"player_id": player_id},
    )
    return "/friends"


@callback(
    Output("friends-refresh", "pathname", allow_duplicate=True),
    Input({"type": "friend-request-decline", "request_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def decline_friend_request(n_clicks_list):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(n_clicks_list):
        return dash.no_update

    player_id = session.get("player_id")
    requests.post(
        f"{API_BASE_URL}/friends/requests/{triggered_id['request_id']}/decline",
        params={"player_id": player_id},
    )
    return "/friends"