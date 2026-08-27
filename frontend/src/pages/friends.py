# target path: frontend/src/pages/friends.py (full replacement)
import time

import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/friends", name="Friends")


# Tournament-style pill subnav shared between this page and friends.py --
# real navigation (dcc.Link, not a client-side tab toggle), since these
# stay two separate registered pages/routes each with their own auth
# check and callbacks rather than being merged into one. Same "small
# duplicated pure render function per page" convention as the rest of
# the app (see analysis.py/profile.py's own docstrings) rather than
# cross-importing. The bottom nav's own "Account" tab still just links
# straight to /my-account -- this subnav is what actually lets you get
# to Friends from there (and back) without a fifth bottom-nav icon.
_ACCOUNT_TAB_BASE = "t3g-tournament-tab"
_ACCOUNT_TAB_ACTIVE = "t3g-tournament-tab t3g-tournament-tab--active"


def _account_subnav(active):
    return html.Div(
        className="t3g-tournament-subnav",
        children=html.Div(
            className="t3g-tournament-subnav-inner",
            children=html.Div(
                className="t3g-tournament-tabs",
                children=[
                    dcc.Link(
                        "My Account",
                        href="/my-account",
                        className=_ACCOUNT_TAB_ACTIVE if active == "account" else _ACCOUNT_TAB_BASE,
                    ),
                    dcc.Link(
                        "My Profile",
                        href="/my-account/profile",
                        className=_ACCOUNT_TAB_ACTIVE if active == "profile" else _ACCOUNT_TAB_BASE,
                    ),
                    dcc.Link(
                        "Friends",
                        href="/friends",
                        className=_ACCOUNT_TAB_ACTIVE if active == "friends" else _ACCOUNT_TAB_BASE,
                    ),
                ],
            ),
        ),
    )


def _refresh_href():
    # dcc.Location only actually reloads the browser when the value it's
    # given differs from what's already loaded -- outputting the same
    # "/friends" pathname while already sitting on /friends is a no-op, so
    # nothing visibly happens even though refresh=True is set. Appending a
    # cache-busting query string guarantees the value always changes,
    # which is what actually makes the refresh fire every time.
    return f"/friends?_r={time.time()}"


def _player_label(player):
    return player.get("nickname") or f"{player.get('first_name', '')} {player.get('surname', '')}".strip()


def _request_row(other_player, action_children, subtitle=None):
    # subtitle is only ever used by _clubmate_row below (the shared
    # club(s) this suggestion came from) -- every other caller leaves it
    # unset, so the plain single-line row (incoming/outgoing requests)
    # keeps rendering exactly as before.
    name_children = [html.Span(_player_label(other_player), className="t3g-friend-request-name")]
    if subtitle:
        name_children.append(html.Span(subtitle, className="t3g-friend-request-subtitle"))
    return html.Div(
        className="t3g-friend-request-row",
        children=[
            html.Div(name_children, className="t3g-friend-request-name-group"),
            html.Div(action_children, className="t3g-friend-request-actions"),
        ],
    )


def _friend_row(friend):
    # Not built on top of _request_row (unlike everywhere else that
    # shares it) -- a confirmed friend's name links through to their
    # profile page (see pages/profile.py), which a still-pending
    # request's name shouldn't, since the backend would just 403 a
    # non-friend trying to load one anyway.
    return html.Div(
        className="t3g-friend-request-row",
        children=[
            dcc.Link(
                _player_label(friend),
                href=f"/players/{friend['player_id']}",
                className="t3g-friend-request-name t3g-friend-request-name-link",
            ),
            html.Div(
                html.Button(
                    "Remove",
                    id={"type": "friend-remove", "friend_id": friend["player_id"]},
                    className="t3g-panel-action-button t3g-panel-action-button--secondary",
                    n_clicks=0,
                ),
                className="t3g-friend-request-actions",
            ),
        ],
    )


def _clubmate_row(clubmate):
    # Same _request_row shell everything else on this page uses, but the
    # action is a single "Add" button rather than the request-row's
    # usual Accept/Decline or Cancel pair -- clicking it sends the
    # request immediately (see send_clubmate_friend_request below), no
    # confirmation modal in between, since seeing someone's real name
    # right here already gives the same reassurance the "who did this
    # go to" confirmation modal exists to give the Player-ID flow below.
    club_names = ", ".join(clubmate.get("club_names") or [])
    return _request_row(
        clubmate,
        html.Button(
            "Add",
            id={"type": "clubmate-add", "player_id": clubmate["player_id"]},
            className="t3g-panel-action-button",
            n_clicks=0,
        ),
        subtitle=club_names,
    )


def layout(**kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="friends-redirect-signin", refresh=True)

    requests_resp = requests.get(f"{API_BASE_URL}/friends/requests/{player_id}")
    pending = requests_resp.json() if requests_resp.status_code == 200 else {"incoming": [], "outgoing": []}
    incoming = pending.get("incoming", [])
    outgoing = pending.get("outgoing", [])

    friends_resp = requests.get(f"{API_BASE_URL}/friends/player/{player_id}")
    friends = friends_resp.json() if friends_resp.status_code == 200 else []

    clubmates_resp = requests.get(f"{API_BASE_URL}/friends/clubmates/{player_id}")
    clubmates = clubmates_resp.json() if clubmates_resp.status_code == 200 else []

    if clubmates:
        clubmates_section = html.Div(
            className="t3g-friend-request-list",
            children=[_clubmate_row(c) for c in clubmates],
        )
    else:
        # Covers both "not in any clubs yet" and "already friends with
        # (or already have a pending request with) everyone you share a
        # club with" -- no need to tell those two apart here, since
        # either way there's nothing left to suggest.
        clubmates_section = html.P(
            "No one new to add from your clubs right now.", className="t3g-empty-state"
        )

    if incoming:
        received_section = html.Div(
            className="t3g-friend-request-list",
            children=[
                _request_row(
                    r["requester"],
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
        received_section = html.P("No requests received.", className="t3g-empty-state")

    if outgoing:
        sent_section = html.Div(
            className="t3g-friend-request-list",
            children=[
                _request_row(
                    r["recipient"],
                    html.Button(
                        "Cancel",
                        id={"type": "friend-request-cancel", "request_id": r["id"]},
                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                        n_clicks=0,
                    ),
                )
                for r in outgoing
            ],
        )
    else:
        sent_section = html.P("No requests sent.", className="t3g-empty-state")

    if friends:
        friends_section = html.Div(
            className="t3g-friend-request-list",
            children=[_friend_row(f) for f in friends],
        )
    else:
        friends_section = html.P("You haven't added any friends yet.", className="t3g-empty-state")

    return html.Div(
        className="t3g-page",
        children=[
            _account_subnav("friends"),
            dcc.Location(id="friends-refresh", refresh=True),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("Add from your Clubs"),
                    html.Div(clubmates_section, className="t3g-panel-body"),
                ],
            ),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("Add by Player ID"),
                    html.Div(
                        className="t3g-panel-body",
                        children=[
                            html.P(
                                "For anyone you don't share a club with -- ask your friend "
                                "for their Player ID, it's on their My Account page.",
                                className="t3g-empty-state mb-2",
                            ),
                            dbc.Input(
                                id="friend-request-player-id",
                                placeholder="Player ID",
                                type="text",
                                className="mb-2",
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
                    html.Div(
                        className="t3g-panel-body",
                        children=[
                            html.H5("Received", className="t3g-friend-subheading"),
                            received_section,
                            html.H5("Sent", className="t3g-friend-subheading t3g-friend-subheading--spaced"),
                            sent_section,
                        ],
                    ),
                ],
            ),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("Your Friends"),
                    html.Div(friends_section, className="t3g-panel-body"),
                ],
            ),
            dbc.Modal(
                id="friend-request-sent-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Friend Request Sent")),
                    dbc.ModalBody(id="friend-request-sent-modal-body"),
                    dbc.ModalFooter(
                        dbc.Button("OK", id="friend-request-sent-ok", color="primary"),
                    ),
                ],
            ),
        ],
    )


@callback(
    Output("friend-request-error", "children"),
    Output("friend-request-sent-modal", "is_open"),
    Output("friend-request-sent-modal-body", "children"),
    Input("friend-request-send", "n_clicks"),
    State("friend-request-player-id", "value"),
    prevent_initial_call=True,
)
def send_friend_request(n_clicks, recipient_id):
    if not recipient_id or not recipient_id.strip():
        return "Enter a player ID.", False, dash.no_update

    player_id = session.get("player_id")
    response = requests.post(
        f"{API_BASE_URL}/friends/requests",
        json={"requester_id": player_id, "recipient_id": recipient_id.strip()},
    )

    if response.status_code == 201:
        # Confirming who it went to (rather than just "Request sent") is
        # what actually makes this feel like it worked, especially now
        # that the input was a raw ID rather than a name picked off a
        # list -- the backend attaches the recipient's name to the
        # response for exactly this.
        recipient = response.json().get("recipient") or {}
        recipient_label = _player_label(recipient) or "that player"
        return "", True, f"Your friend request to {recipient_label} has been sent."

    try:
        payload = response.json()
        detail = payload.get("detail", "Couldn't send that friend request.")
        if not isinstance(detail, str):
            # FastAPI's own validation errors (e.g. a malformed/non-UUID
            # ID) put a list of error objects in "detail", not a string --
            # rendering that directly as Dash children would error.
            detail = "That doesn't look like a valid player ID."
    except ValueError:
        detail = "Couldn't send that friend request."
    return detail, False, dash.no_update


@callback(
    Output("friend-request-sent-modal", "is_open", allow_duplicate=True),
    Output("friends-refresh", "href"),
    Input("friend-request-sent-ok", "n_clicks"),
    prevent_initial_call=True,
)
def close_friend_request_sent_modal(n_clicks):
    # The page only actually refreshes once the person has acknowledged
    # the confirmation -- refreshing immediately on send (the old
    # behavior) swapped the whole page out right as the button was
    # clicked, which is exactly why it wasn't clear anything had happened.
    return False, _refresh_href()


@callback(
    Output("friends-refresh", "href", allow_duplicate=True),
    Input({"type": "clubmate-add", "player_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def send_clubmate_friend_request(n_clicks_list):
    # No confirmation modal here, unlike send_friend_request above --
    # that one exists because typing a Player ID by hand has real typo
    # risk ("did this actually go to who I meant?"). Clicking Add next to
    # a name you can already see doesn't have that problem, so this just
    # refreshes straight away, same immediate-refresh pattern as accept/
    # decline/cancel/remove below.
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(n_clicks_list):
        return dash.no_update

    player_id = session.get("player_id")
    requests.post(
        f"{API_BASE_URL}/friends/requests",
        json={"requester_id": player_id, "recipient_id": triggered_id["player_id"]},
    )
    return _refresh_href()


@callback(
    Output("friends-refresh", "href", allow_duplicate=True),
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
    return _refresh_href()


@callback(
    Output("friends-refresh", "href", allow_duplicate=True),
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
    return _refresh_href()


@callback(
    Output("friends-refresh", "href", allow_duplicate=True),
    Input({"type": "friend-request-cancel", "request_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def cancel_friend_request(n_clicks_list):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(n_clicks_list):
        return dash.no_update

    player_id = session.get("player_id")
    requests.delete(
        f"{API_BASE_URL}/friends/requests/{triggered_id['request_id']}",
        params={"player_id": player_id},
    )
    return _refresh_href()


@callback(
    Output("friends-refresh", "href", allow_duplicate=True),
    Input({"type": "friend-remove", "friend_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def remove_friend(n_clicks_list):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(n_clicks_list):
        return dash.no_update

    player_id = session.get("player_id")
    requests.delete(
        f"{API_BASE_URL}/friends/{triggered_id['friend_id']}",
        params={"player_id": player_id},
    )
    return _refresh_href()