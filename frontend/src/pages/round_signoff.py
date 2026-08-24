# target path: frontend/src/pages/round_signoff.py (new file)
"""
The dedicated review panel behind the navbar's sign-off pill
(layouts/navbar.py's round-signoff-indicator) -- every round this player
is an accepted participant in that's sitting in pending_signoff and that
they personally haven't approved yet (GET /rounds/pending-signoff/
{player_id}, see list_pending_signoff_rounds in backend/services/
rounds.py).

Reached only via that pill (and a direct URL) -- deliberately not added
to the top nav links in layouts/navbar.py, same as /live-round isn't --
it's a transient "you have something to do" destination, not a permanent
section of the app, and the pill already disappears on its own once
there's nothing left here.

Not registered as a page anyone can browse to and see other players' open
items -- the backend endpoint this reads from is scoped to whichever
player_id is asked for, and this page only ever asks for the signed-in
session's own player_id, same pattern as every other player-scoped page
in this app (analysis.py, home.py, etc.).
"""
import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from components.scorecard import (
    history_score_mark_class,
    player_signoff_status_badge,
    round_header_label,
    tournament_round_badge,
)
from config import API_BASE_URL

dash.register_page(__name__, path="/round-signoff", name="Sign Off Rounds")


def _fairway_cell(hole):
    if hole.get("par") == 3:
        return html.Td(html.Span("—", className="t3g-history-fairway-na"))
    hit = hole.get("fairway_hit")
    if hit is None:
        return html.Td(html.Span("—", className="t3g-history-fairway-na"))
    if hit:
        return html.Td(html.Span("Y", className="t3g-history-fairway-yes"))
    return html.Td(html.Span("N", className="t3g-history-fairway-no"))


def _fairway_summary(hole_subset):
    eligible = [h for h in hole_subset if h.get("par") != 3 and h.get("fairway_hit") is not None]
    if not eligible:
        return "—"
    hit = sum(1 for h in eligible if h.get("fairway_hit"))
    return f"{hit}/{len(eligible)}"


def _sum_field(hole_subset, field):
    values = [h.get(field) for h in hole_subset if h.get(field) is not None]
    return sum(values) if values else None


def _player_display_name(player):
    return player.get("nickname") or f"{player.get('first_name') or ''} {player.get('surname') or ''}".strip() or "Player"


def _player_mini_scorecard(player):
    """A player's full 18-hole scorecard within a round awaiting sign-off
    -- Hole/Par/Strokes/Putts/Fairway only (no HCP/NET/Stableford columns
    the way analysis.py's fuller version has, since those need each
    player's own Handicap Index and a pending_signoff round hasn't
    affected anyone's handicap yet -- there's nothing meaningful to net
    against here). This is "the whole scorecard" each player is meant to
    be checking over before approving it -- every entered score, not just
    a total."""
    holes_by_number = {h["hole_number"]: h for h in (player.get("holes") or [])}
    front9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(1, 10)]
    back9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(10, 19)]

    out_par, in_par = _sum_field(front9, "par"), _sum_field(back9, "par")
    tot_par = out_par + in_par if out_par is not None and in_par is not None else None
    out_strokes, in_strokes = _sum_field(front9, "strokes"), _sum_field(back9, "strokes")
    tot_strokes = out_strokes + in_strokes if out_strokes is not None and in_strokes is not None else None
    out_putts, in_putts = _sum_field(front9, "putts"), _sum_field(back9, "putts")
    tot_putts = out_putts + in_putts if out_putts is not None and in_putts is not None else None

    def _hole_number_cells(hole_subset):
        return [html.Th(str(h["hole_number"])) for h in hole_subset]

    def _plain_cells(hole_subset, field):
        return [html.Td(h.get(field) if h.get(field) is not None else "—") for h in hole_subset]

    def _score_cells(hole_subset):
        return [
            html.Td(
                html.Span(
                    h.get("strokes") if h.get("strokes") is not None else "—",
                    className=history_score_mark_class(h.get("strokes"), h.get("par")),
                )
            )
            for h in hole_subset
        ]

    def _fairway_cells(hole_subset):
        return [_fairway_cell(h) for h in hole_subset]

    header_row = html.Tr(
        [html.Th("Hole", className="t3g-history-row-label")]
        + _hole_number_cells(front9)
        + [html.Th("OUT")]
        + _hole_number_cells(back9)
        + [html.Th("IN"), html.Th("TOT")]
    )
    par_row = html.Tr(
        className="t3g-history-par-row",
        children=(
            [html.Td("Par", className="t3g-history-row-label")]
            + _plain_cells(front9, "par")
            + [html.Td(out_par if out_par is not None else "—", className="t3g-history-summary-cell")]
            + _plain_cells(back9, "par")
            + [
                html.Td(in_par if in_par is not None else "—", className="t3g-history-summary-cell"),
                html.Td(tot_par if tot_par is not None else "—", className="t3g-history-summary-cell"),
            ]
        ),
    )
    score_row = html.Tr(
        className="t3g-history-player-row",
        children=(
            [html.Td("Strokes", className="t3g-history-row-label")]
            + _score_cells(front9)
            + [html.Td(out_strokes if out_strokes is not None else "—", className="t3g-history-summary-cell")]
            + _score_cells(back9)
            + [
                html.Td(in_strokes if in_strokes is not None else "—", className="t3g-history-summary-cell"),
                html.Td(tot_strokes if tot_strokes is not None else "—", className="t3g-history-summary-cell"),
            ]
        ),
    )
    putts_row = html.Tr(
        className="t3g-history-detail-row",
        children=(
            [html.Td("Putts", className="t3g-history-row-label")]
            + _plain_cells(front9, "putts")
            + [html.Td(out_putts if out_putts is not None else "—", className="t3g-history-summary-cell")]
            + _plain_cells(back9, "putts")
            + [
                html.Td(in_putts if in_putts is not None else "—", className="t3g-history-summary-cell"),
                html.Td(tot_putts if tot_putts is not None else "—", className="t3g-history-summary-cell"),
            ]
        ),
    )
    fairway_row = html.Tr(
        className="t3g-history-detail-row",
        children=(
            [html.Td("Fairway", className="t3g-history-row-label")]
            + _fairway_cells(front9)
            + [html.Td(_fairway_summary(front9), className="t3g-history-summary-cell")]
            + _fairway_cells(back9)
            + [
                html.Td(_fairway_summary(back9), className="t3g-history-summary-cell"),
                html.Td(_fairway_summary(front9 + back9), className="t3g-history-summary-cell"),
            ]
        ),
    )

    return html.Div(
        className="t3g-signoff-player-scorecard",
        children=[
            html.Div(
                [
                    html.Span(_player_display_name(player), className="t3g-signoff-player-name"),
                    player_signoff_status_badge(player.get("signed_off_at")),
                ],
                className="t3g-signoff-player-scorecard-header",
            ),
            html.Div(
                className="t3g-history-scorecard-wrap",
                children=html.Table(
                    className="t3g-history-scorecard-table",
                    children=[
                        html.Thead([header_row, par_row]),
                        html.Tbody([score_row, putts_row, fairway_row]),
                    ],
                ),
            ),
        ],
    )


def _round_signoff_card(round_data, viewer_player_id):
    round_id = round_data["id"]
    players = round_data.get("players") or []
    viewer = next((p for p in players if p["player_id"] == viewer_player_id), None)
    viewer_already_signed = bool(viewer and viewer.get("signed_off_at"))

    header_actions = []
    if round_data.get("tournament_id"):
        header_actions.append(tournament_round_badge())

    still_waiting_on = [
        _player_display_name(p) for p in players if not p.get("signed_off_at") and p["player_id"] != viewer_player_id
    ]
    if still_waiting_on:
        waiting_note = "Also waiting on: " + ", ".join(still_waiting_on)
    else:
        waiting_note = "Everyone else has already signed off -- you're the last one."

    return html.Div(
        className="t3g-round-card t3g-signoff-card",
        children=[
            html.Div(
                className="t3g-round-card-header",
                children=[
                    html.Span(round_header_label(round_data), className="t3g-round-card-title"),
                    html.Div(header_actions, className="t3g-round-card-header-actions"),
                ],
            ),
            html.Div(waiting_note, className="t3g-signoff-waiting-note"),
            html.Div(
                className="t3g-signoff-scorecards",
                children=[_player_mini_scorecard(p) for p in players],
            ),
            html.Div(
                className="t3g-signoff-actions",
                children=[
                    html.Button(
                        "Approve Scorecard",
                        id={"type": "signoff-approve", "round_id": round_id},
                        className="t3g-panel-action-button",
                        n_clicks=0,
                        disabled=viewer_already_signed,
                    ),
                    html.Button(
                        "Reject / Send Back for Edits",
                        id={"type": "signoff-reject", "round_id": round_id},
                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                        n_clicks=0,
                    ),
                ],
            ),
        ],
    )


def layout(**kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="round-signoff-redirect-signin", refresh=True)

    response = requests.get(f"{API_BASE_URL}/rounds/pending-signoff/{player_id}")
    pending_rounds = response.json() if response.status_code == 200 else []

    return html.Div(
        className="t3g-page",
        children=[
            dcc.Store(id="signoff-store", data=pending_rounds),
            dcc.Store(id="signoff-player-store", data=player_id),
            dcc.Store(id="signoff-reject-target"),
            # href (not pathname) -- pathname alone drops query strings on
            # a refresh=True redirect (see the fix noted in app.py's other
            # redirect components), and the reject redirect below needs to
            # carry ?round_id=&view=full, not just a bare path.
            dcc.Location(id="signoff-redirect", refresh=True),
            html.Div(
                className="t3g-panel",
                children=[
                    html.Div(
                        className="t3g-panel-navbar",
                        children=html.H3("Sign Off Rounds", className="t3g-panel-navbar-title"),
                    ),
                    html.Div(
                        className="t3g-panel-body",
                        children=html.Div(
                            id="signoff-list",
                            children=html.P(
                                "Approving this round submits your sign-off. Once every player has approved, "
                                "the round is finalized and (for a round with other players) starts counting "
                                "toward everyone's Handicap Index. Rejecting reopens it for edits and clears "
                                "everyone's sign-off, including any that already came in.",
                                className="t3g-empty-state",
                            ) if not pending_rounds else None,
                        ),
                    ),
                ],
            ),
            dbc.Modal(
                id="signoff-reject-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Send this round back for edits?")),
                    dbc.ModalBody(
                        "This reopens the round's scorecard for editing and clears everyone's sign-off, "
                        "including anyone who already approved it -- they'll need to review it again once "
                        "it's resubmitted."
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Cancel", id="signoff-reject-cancel", color="secondary"),
                            dbc.Button("Reject", id="signoff-reject-confirm", color="danger"),
                        ]
                    ),
                ],
            ),
        ],
    )


@callback(
    Output("signoff-list", "children"),
    Input("signoff-store", "data"),
    State("signoff-player-store", "data"),
)
def render_signoff_list(pending_rounds, player_id):
    if not pending_rounds:
        return html.P("Nothing waiting on your sign-off right now.", className="t3g-empty-state")

    return html.Div(
        className="t3g-scoring-history-list",
        children=[_round_signoff_card(r, player_id) for r in pending_rounds],
    )


@callback(
    Output("signoff-store", "data"),
    Output("signoff-redirect", "href"),
    Input({"type": "signoff-approve", "round_id": ALL}, "n_clicks"),
    State("signoff-store", "data"),
    State("signoff-player-store", "data"),
    prevent_initial_call=True,
)
def approve_signoff(approve_clicks, pending_rounds, player_id):
    triggered_id = dash.ctx.triggered_id
    if not isinstance(triggered_id, dict) or triggered_id.get("type") != "signoff-approve":
        raise PreventUpdate
    if not any(approve_clicks):
        # The set of buttons re-rendering (e.g. after another round in the
        # list is removed) also fires this -- only actually act on a real
        # click, same guard as analysis.py's delete-modal callback.
        raise PreventUpdate

    round_id = triggered_id["round_id"]
    response = requests.post(f"{API_BASE_URL}/rounds/{round_id}/players/{player_id}/signoff")

    if response.status_code != 200:
        # Someone else already changed this round's state from under us
        # (e.g. rejected it right before this click landed) -- leave the
        # list as-is rather than silently pretending it worked; the
        # navbar pill's next poll will reconcile it either way.
        raise PreventUpdate

    remaining = [r for r in (pending_rounds or []) if r["id"] != round_id]
    # Approving takes you straight back to home -- there's nothing else to
    # do here for this round once you've signed off on it, regardless of
    # whether anything else is left in the list.
    return remaining, "/"


@callback(
    Output("signoff-reject-modal", "is_open"),
    Output("signoff-reject-target", "data"),
    Input({"type": "signoff-reject", "round_id": ALL}, "n_clicks"),
    Input("signoff-reject-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_reject_modal(reject_clicks, cancel_clicks):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "signoff-reject-cancel":
        return False, None

    if isinstance(triggered_id, dict) and triggered_id.get("type") == "signoff-reject":
        if not any(reject_clicks):
            raise PreventUpdate
        return True, {"round_id": triggered_id["round_id"]}

    raise PreventUpdate


@callback(
    Output("signoff-store", "data", allow_duplicate=True),
    Output("signoff-reject-modal", "is_open", allow_duplicate=True),
    Output("signoff-redirect", "href", allow_duplicate=True),
    Input("signoff-reject-confirm", "n_clicks"),
    State("signoff-reject-target", "data"),
    State("signoff-store", "data"),
    State("signoff-player-store", "data"),
    prevent_initial_call=True,
)
def confirm_reject(n_clicks, target, pending_rounds, player_id):
    if not target or not target.get("round_id"):
        raise PreventUpdate

    round_id = target["round_id"]
    response = requests.post(f"{API_BASE_URL}/rounds/{round_id}/players/{player_id}/reject")

    if response.status_code != 200:
        raise PreventUpdate

    remaining = [r for r in (pending_rounds or []) if r["id"] != round_id]
    # Rejecting reopens the round for edits -- send the rejecter straight
    # into its Full Scorecard view (not the usual Hole by Hole default) so
    # they can see every hole at once to find what needs fixing, instead
    # of clicking through hole by hole -- see live_round.py's layout() and
    # render_live_round_body's initial_view param.
    return remaining, False, f"/live-round?round_id={round_id}&view=full"