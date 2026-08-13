# target path: frontend/src/pages/club.py (replace entire file -- previous copy on disk has its whole
# contents duplicated back-to-back, which is what's causing "Duplicate callback outputs" again; this
# is a single clean copy, don't paste it on top of the old one)
import dash
import dash_bootstrap_components as dbc
import requests
from dash import Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path_template="/clubs/<slug>", name="Club")

_SORT_BUTTON_BASE = "t3g-panel-action-button t3g-panel-action-button--secondary"
_SORT_BUTTON_ACTIVE = "t3g-panel-action-button"


def _player_label(row):
    return f"{row.get('first_name', '')} {row.get('surname', '')}".strip() or "Unknown player"


def _handicap_value(row):
    latest = row.get("latest_handicap")
    return latest["handicap"] if latest else None


def _format_handicap(row):
    value = _handicap_value(row)
    return f"{value}" if value is not None else "Not set"


def _sort_directory(directory, sort_by):
    if sort_by == "handicap":
        # Players with no handicap set sort to the bottom regardless of
        # direction, rather than landing at 0/first -- "Not set" isn't a
        # real handicap value and shouldn't look like the best one.
        return sorted(directory, key=lambda row: (_handicap_value(row) is None, _handicap_value(row) or 0))
    return sorted(directory, key=lambda row: _player_label(row).lower())


def _directory_table(directory, sort_by):
    ordered = _sort_directory(directory, sort_by)

    if not ordered:
        return html.P("No players in this club yet.", className="t3g-empty-state")

    rows = [
        html.Tr([html.Td(_player_label(row)), html.Td(_format_handicap(row))])
        for row in ordered
    ]

    return dbc.Table(
        [
            html.Thead(html.Tr([html.Th("Name"), html.Th("Handicap")])),
            html.Tbody(rows),
        ],
        className="t3g-club-directory-table",
        bordered=False,
        hover=True,
    )


def _admin_banner(is_admin):
    # Replaces the old bordered name/description panel's inline "ADMIN"
    # pill -- this is its own slim, full-width strip (styled after
    # layouts/subnav.py) so it reads as a status banner, not just a badge
    # buried in a box. Renders nothing at all for non-admins.
    if not is_admin:
        return None

    return html.Div(
        className="t3g-club-admin-banner",
        children=html.Div(
            "You're this club's admin",
            className="t3g-club-admin-banner-inner",
        ),
    )


def _invite_panel(club, player_id):
    """Only the club's admin sees this -- invites are the only way into a
    club now (see backend/services/club_invites.py), so this is the one
    place membership actually grows from."""
    if not player_id or str(club.get("club_admin")) != player_id:
        return None

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Invite a Player"),
            html.Div(
                className="t3g-panel-body",
                children=[
                    html.P(
                        "Ask them for their Player ID -- it's on their My Account page.",
                        className="t3g-empty-state mb-2",
                    ),
                    dbc.Input(
                        id="club-invite-player-id",
                        placeholder="Player ID",
                        type="text",
                        className="mb-2",
                    ),
                    html.Div(id="club-invite-send-error", className="text-danger mt-2"),
                    html.Button(
                        "Send Invite",
                        id="club-invite-send",
                        className="t3g-panel-action-button mt-2",
                        n_clicks=0,
                    ),
                ],
            ),
        ],
    )


def _not_found_page():
    return html.Div(
        className="t3g-page",
        children=html.Div(
            className="t3g-panel",
            children=html.Div(
                html.P("Club not found.", className="t3g-empty-state"),
                className="t3g-panel-body",
            ),
        ),
    )


def layout(slug=None, **kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="club-redirect-signin", refresh=True)

    if not slug:
        return _not_found_page()

    club_resp = requests.get(f"{API_BASE_URL}/clubs/slug/{slug}")
    if club_resp.status_code != 200:
        return _not_found_page()
    club = club_resp.json()

    is_admin = bool(player_id) and str(club.get("club_admin")) == player_id

    directory_resp = requests.get(f"{API_BASE_URL}/handicaps/club/{club['id']}/latest")
    directory = directory_resp.json() if directory_resp.status_code == 200 else []

    return html.Div(
        className="t3g-page",
        children=[
            dcc.Store(id="club-id-store", data=club["id"]),
            _admin_banner(is_admin),
            html.H2(club.get("name", "Club"), className="t3g-club-heading"),
            _invite_panel(club, player_id),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar(
                        "Player Directory",
                        action=[
                            html.Button(
                                "Name",
                                id="club-directory-sort-name",
                                className=_SORT_BUTTON_ACTIVE,
                                n_clicks=0,
                            ),
                            html.Button(
                                "Handicap",
                                id="club-directory-sort-handicap",
                                className=_SORT_BUTTON_BASE,
                                n_clicks=0,
                            ),
                        ],
                    ),
                    dcc.Store(id="club-directory-store", data=directory),
                    html.Div(
                        id="club-directory-content",
                        className="t3g-panel-body",
                        children=_directory_table(directory, "name"),
                    ),
                ],
            ),
            # Previous Tournaments and Upcoming Tournament panels come in a
            # later pass -- there's no tournaments table/API yet (see the
            # backlog). This page is just the player directory for now.
            dbc.Modal(
                id="club-invite-sent-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Invite Sent")),
                    dbc.ModalBody(id="club-invite-sent-modal-body"),
                    dbc.ModalFooter(dbc.Button("OK", id="club-invite-sent-ok", color="primary")),
                ],
            ),
        ],
    )


@callback(
    Output("club-directory-content", "children"),
    Output("club-directory-sort-name", "className"),
    Output("club-directory-sort-handicap", "className"),
    Input("club-directory-sort-name", "n_clicks"),
    Input("club-directory-sort-handicap", "n_clicks"),
    State("club-directory-store", "data"),
    prevent_initial_call=True,
)
def sort_club_directory(name_clicks, handicap_clicks, directory):
    triggered_id = dash.ctx.triggered_id
    sort_by = "handicap" if triggered_id == "club-directory-sort-handicap" else "name"
    table = _directory_table(directory or [], sort_by)

    if sort_by == "handicap":
        return table, _SORT_BUTTON_BASE, _SORT_BUTTON_ACTIVE
    return table, _SORT_BUTTON_ACTIVE, _SORT_BUTTON_BASE


@callback(
    Output("club-invite-send-error", "children"),
    Output("club-invite-sent-modal", "is_open"),
    Output("club-invite-sent-modal-body", "children"),
    Input("club-invite-send", "n_clicks"),
    State("club-invite-player-id", "value"),
    State("club-id-store", "data"),
    prevent_initial_call=True,
)
def send_club_invite_callback(n_clicks, invitee_id, club_id):
    if not invitee_id or not invitee_id.strip():
        return "Enter a player ID.", False, dash.no_update

    player_id = session.get("player_id")
    response = requests.post(
        f"{API_BASE_URL}/club-invites/",
        json={"club_id": club_id, "inviter_id": player_id, "invitee_id": invitee_id.strip()},
    )

    if response.status_code == 201:
        invitee = response.json().get("invitee") or {}
        invitee_label = (
            invitee.get("nickname")
            or f"{invitee.get('first_name', '')} {invitee.get('surname', '')}".strip()
            or "that player"
        )
        return "", True, f"Your invite to {invitee_label} has been sent."

    try:
        payload = response.json()
        detail = payload.get("detail", "Couldn't send that invite.")
        if not isinstance(detail, str):
            detail = "That doesn't look like a valid player ID."
    except ValueError:
        detail = "Couldn't send that invite."
    return detail, False, dash.no_update


@callback(
    Output("club-invite-sent-modal", "is_open", allow_duplicate=True),
    Output("club-invite-player-id", "value"),
    Input("club-invite-sent-ok", "n_clicks"),
    prevent_initial_call=True,
)
def close_club_invite_sent_modal(n_clicks):
    return False, ""