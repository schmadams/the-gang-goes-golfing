# target path: frontend/src/pages/tournament.py (new file)
import time

import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from flask import session

from components.scorecard import live_badge
from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(
    __name__,
    path_template="/clubs/<slug>/tournaments/<tournament_id>",
    name="Tournament",
)

# Same labels as club.py's create-tournament modal -- duplicated rather
# than shared, matching how this app already keeps small per-page copies
# (e.g. _course_label) instead of a shared constants module.
_TOURNAMENT_FORMAT_LABELS = {
    "scratch": "Scratch",
    "stableford": "Stableford",
    "net": "Net",
    "2bbb": "2BBB (Better Ball)",
    "4bbb": "4BBB (Better Ball)",
    "texas_scramble": "Texas Scramble",
}


def _entrant_label(entrant):
    return (
        entrant.get("nickname")
        or f"{entrant.get('first_name', '')} {entrant.get('surname', '')}".strip()
        or "Unknown player"
    )


def _not_found_page():
    return html.Div(
        className="t3g-page",
        children=html.Div(
            className="t3g-panel",
            children=html.Div(
                html.P("Tournament not found.", className="t3g-empty-state"),
                className="t3g-panel-body",
            ),
        ),
    )


def _tournament_info_panel(tournament):
    rounds = tournament.get("rounds", [])
    round_items = [
        html.Li(
            f"Round {r['round_number']}: {r.get('round_date', '')} — "
            + (r.get("club_name") or "Course TBC")
            + (f", {r['course_name']}" if r.get("course_name") else "")
            + (f" ({r['tee_name']} tees)" if r.get("tee_name") else ""),
        )
        for r in rounds
    ] or [html.Li("No rounds set up yet.", className="t3g-empty-state")]

    entry_mode = tournament.get("entry_mode", "self")
    entry_description = (
        "Anyone in the club can enter directly."
        if entry_mode == "self"
        else "Entries need the admin's approval."
    )

    min_hcp = tournament.get("min_handicap")
    max_hcp = tournament.get("max_handicap")
    range_text = None
    if min_hcp is not None or max_hcp is not None:
        range_text = (
            f"Handicap range: {min_hcp if min_hcp is not None else '-'} "
            f"to {max_hcp if max_hcp is not None else '-'}"
        )

    title_children = [html.Span(tournament.get("name", "Tournament"), className="t3g-tournament-card-title")]
    if tournament.get("status") == "in_progress":
        title_children.append(live_badge())

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Tournament Info"),
            html.Div(
                className="t3g-panel-body",
                children=[
                    html.Div(title_children, className="t3g-tournament-card-header mb-2"),
                    html.Span(
                        _TOURNAMENT_FORMAT_LABELS.get(tournament.get("format"), tournament.get("format")),
                        className="t3g-tournament-format-badge",
                    ),
                    html.P(entry_description, className="t3g-empty-state mt-2 mb-1"),
                    html.P(range_text, className="t3g-empty-state mb-2") if range_text else None,
                    html.Ul(round_items, className="t3g-tournament-round-list"),
                ],
            ),
        ],
    )


def _my_entry_section(tournament, my_entry):
    """Always renders both the enter/apply and withdraw buttons (visibility
    toggled with a style, not by leaving one out of the tree) -- handle_
    tournament_entry below has both as Inputs, and a button that's
    conditionally absent from the DOM instead of just hidden would make
    that registration invalid on renders where it's missing."""
    entry_mode = tournament.get("entry_mode", "self")
    status = my_entry["status"] if my_entry else None

    show_enter = status in (None, "rejected", "withdrawn")
    show_withdraw = status in ("pending", "confirmed")

    if status is None:
        enter_label = "Enter Tournament" if entry_mode == "self" else "Apply to Enter"
    else:
        enter_label = "Enter Again" if entry_mode == "self" else "Apply Again"

    status_message = {
        "pending": "Your application is pending approval.",
        "confirmed": "You're entered in this tournament.",
        "rejected": "Your application wasn't accepted.",
        "withdrawn": "You've withdrawn from this tournament.",
    }.get(status)

    return html.Div(
        className="t3g-tournament-my-entry mb-3",
        children=[
            html.P(status_message, className="t3g-empty-state mb-2") if status_message else None,
            html.Button(
                enter_label,
                id="tournament-enter-button",
                className="t3g-panel-action-button",
                n_clicks=0,
                style={} if show_enter else {"display": "none"},
            ),
            html.Button(
                "Withdraw",
                id="tournament-withdraw-button",
                className="t3g-panel-action-button t3g-panel-action-button--secondary",
                n_clicks=0,
                style={} if show_withdraw else {"display": "none"},
            ),
            html.Div(id="tournament-entry-error", className="text-danger mt-2"),
        ],
    )


def _entrants_panel(tournament, entrants, my_entry, is_admin):
    pending = [e for e in entrants if e["status"] == "pending"]
    confirmed = [e for e in entrants if e["status"] == "confirmed"]

    admin_pending_section = None
    if is_admin and tournament.get("entry_mode") == "approval" and pending:
        admin_pending_section = html.Div(
            className="mb-3",
            children=[
                html.Div("Pending Applications", className="t3g-modal-label t3g-tournament-rounds-label"),
                html.Div(
                    [
                        html.Div(
                            className="t3g-friend-request-row",
                            children=[
                                html.Span(
                                    _entrant_label(e)
                                    + (
                                        f" (hcp {e['handicap_at_entry']})"
                                        if e.get("handicap_at_entry") is not None
                                        else ""
                                    ),
                                    className="t3g-friend-request-name",
                                ),
                                html.Div(
                                    [
                                        html.Button(
                                            "Approve",
                                            id={"type": "tournament-entrant-approve", "player_id": e["player_id"]},
                                            className="t3g-panel-action-button",
                                            n_clicks=0,
                                        ),
                                        html.Button(
                                            "Reject",
                                            id={"type": "tournament-entrant-reject", "player_id": e["player_id"]},
                                            className="t3g-panel-action-button t3g-panel-action-button--secondary",
                                            n_clicks=0,
                                        ),
                                    ],
                                    className="t3g-friend-request-actions",
                                ),
                            ],
                        )
                        for e in pending
                    ],
                    className="t3g-friend-request-list",
                ),
                html.Div(id="tournament-admin-action-error", className="text-danger mt-2"),
            ],
        )

    confirmed_items = [
        html.Li(
            _entrant_label(e)
            + (f" — hcp {e['handicap_at_entry']}" if e.get("handicap_at_entry") is not None else "")
        )
        for e in confirmed
    ] or [html.Li("No confirmed entrants yet.", className="t3g-empty-state")]

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Entrants"),
            html.Div(
                className="t3g-panel-body",
                children=[
                    _my_entry_section(tournament, my_entry),
                    admin_pending_section,
                    html.Div("Confirmed", className="t3g-modal-label t3g-tournament-rounds-label mt-2 mb-1"),
                    html.Ul(confirmed_items, className="t3g-tournament-round-list"),
                ],
            ),
        ],
    )


def _leaderboard_panel(entrants):
    """Placeholder -- no round-to-tournament scoring link exists yet, so
    this just lists confirmed entrants (alphabetically, since there's no
    score to rank by) instead of a real leaderboard. Position/Player/Score
    columns are here so the shape is already right once scoring lands."""
    confirmed = sorted(
        (e for e in entrants if e["status"] == "confirmed"),
        key=lambda e: _entrant_label(e).lower(),
    )

    if not confirmed:
        body = html.P("No entrants yet.", className="t3g-empty-state")
    else:
        rows = [
            html.Tr([html.Td(str(i + 1)), html.Td(_entrant_label(e)), html.Td("-")])
            for i, e in enumerate(confirmed)
        ]
        body = dbc.Table(
            [
                html.Thead(html.Tr([html.Th("Pos"), html.Th("Player"), html.Th("Score")])),
                html.Tbody(rows),
            ],
            className="t3g-club-directory-table",
            bordered=False,
            hover=True,
        )

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Leaderboard"),
            html.Div(
                className="t3g-panel-body",
                children=[
                    html.P(
                        "Scoring isn't wired up yet -- this will rank by score once rounds are linked in.",
                        className="t3g-empty-state mb-2",
                    ),
                    body,
                ],
            ),
        ],
    )


def layout(slug=None, tournament_id=None, **kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="tournament-redirect-signin", refresh=True)

    if not slug or not tournament_id:
        return _not_found_page()

    club_resp = requests.get(f"{API_BASE_URL}/clubs/slug/{slug}")
    if club_resp.status_code != 200:
        return _not_found_page()
    club = club_resp.json()

    tournament_resp = requests.get(f"{API_BASE_URL}/tournaments/{tournament_id}")
    if tournament_resp.status_code != 200:
        return _not_found_page()
    tournament = tournament_resp.json()

    is_admin = bool(player_id) and str(club.get("club_admin")) == player_id
    entrants = tournament.get("entrants", [])
    my_entry = next((e for e in entrants if str(e.get("player_id")) == player_id), None)

    return html.Div(
        className="t3g-page t3g-club-page",
        children=[
            dcc.Store(id="tournament-id-store", data=tournament_id),
            dcc.Link(
                f"← Back to {club.get('name', 'club')}",
                href=f"/clubs/{slug}",
                className="t3g-link-button mb-2",
            ),
            _tournament_info_panel(tournament),
            html.Div(
                className="t3g-panel-grid",
                children=[
                    _entrants_panel(tournament, entrants, my_entry, is_admin),
                    _leaderboard_panel(entrants),
                ],
            ),
            dcc.Location(id="tournament-entry-redirect", refresh=True),
            dcc.Location(id="tournament-admin-action-redirect", refresh=True),
        ],
    )


@callback(
    Output("tournament-entry-error", "children"),
    Output("tournament-entry-redirect", "pathname"),
    Input("tournament-enter-button", "n_clicks"),
    Input("tournament-withdraw-button", "n_clicks"),
    State("tournament-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_tournament_entry(enter_clicks, withdraw_clicks, tournament_id, current_pathname):
    triggered_id = dash.ctx.triggered_id
    player_id = session.get("player_id")

    if triggered_id == "tournament-enter-button":
        response = requests.post(
            f"{API_BASE_URL}/tournaments/{tournament_id}/entrants",
            json={"player_id": player_id},
        )
        if response.status_code == 201:
            return "", f"{current_pathname}?_r={time.time()}"
        try:
            detail = response.json().get("detail", "Couldn't process your entry.")
            if not isinstance(detail, str):
                detail = "Couldn't process your entry."
        except ValueError:
            detail = "Couldn't process your entry."
        return detail, dash.no_update

    if triggered_id == "tournament-withdraw-button":
        response = requests.delete(f"{API_BASE_URL}/tournaments/{tournament_id}/entrants/{player_id}")
        if response.status_code == 200:
            return "", f"{current_pathname}?_r={time.time()}"
        return "Couldn't withdraw. Try again.", dash.no_update

    return dash.no_update, dash.no_update


@callback(
    Output("tournament-admin-action-error", "children"),
    Output("tournament-admin-action-redirect", "pathname"),
    Input({"type": "tournament-entrant-approve", "player_id": ALL}, "n_clicks"),
    Input({"type": "tournament-entrant-reject", "player_id": ALL}, "n_clicks"),
    State("tournament-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_entrant_response(approve_clicks, reject_clicks, tournament_id, current_pathname):
    triggered_id = dash.ctx.triggered_id
    # Same phantom-trigger guard as club.py's edit_tournament_rounds --
    # a fresh Approve/Reject button appearing (e.g. after a different
    # application on the same page gets responded to) can re-fire this
    # callback on its own with no real click behind it.
    if not triggered_id or not (any(approve_clicks or []) or any(reject_clicks or [])):
        return dash.no_update, dash.no_update

    admin_id = session.get("player_id")
    action = "approve" if triggered_id.get("type") == "tournament-entrant-approve" else "reject"
    target_player_id = triggered_id["player_id"]

    response = requests.post(
        f"{API_BASE_URL}/tournaments/{tournament_id}/entrants/{target_player_id}/{action}",
        params={"admin_id": admin_id},
    )
    if response.status_code == 200:
        return "", f"{current_pathname}?_r={time.time()}"

    try:
        detail = response.json().get("detail", "Couldn't process that.")
        if not isinstance(detail, str):
            detail = "Couldn't process that."
    except ValueError:
        detail = "Couldn't process that."
    return detail, dash.no_update