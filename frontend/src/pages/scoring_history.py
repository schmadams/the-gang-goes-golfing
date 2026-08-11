# target path: frontend/src/pages/scoring_history.py (new file)
import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from components.scorecard import format_handicap, history_score_mark_class, live_badge, round_header_label
from config import API_BASE_URL

dash.register_page(__name__, path="/scoring-history", name="Scoring History")


def _fairway_cell(hole):
    # Not a meaningful stat on a par 3 (see live_round.py) -- shown as a
    # dash rather than a false "No".
    if hole.get("par") == 3:
        return html.Td(html.Span("—", className="t3g-history-fairway-na"))
    hit = hole.get("fairway_hit")
    if hit is None:
        return html.Td(html.Span("—", className="t3g-history-fairway-na"))
    if hit:
        return html.Td(html.Span("Y", className="t3g-history-fairway-yes"))
    return html.Td(html.Span("N", className="t3g-history-fairway-no"))


def _fairway_summary(hole_subset):
    # Only par 4s/5s count -- a fraction ("3/5") reads better here than a
    # sum, since the denominator (how many fairways were even in play)
    # varies round to round.
    eligible = [h for h in hole_subset if h.get("par") != 3 and h.get("fairway_hit") is not None]
    if not eligible:
        return "—"
    hit = sum(1 for h in eligible if h.get("fairway_hit"))
    return f"{hit}/{len(eligible)}"


def _round_scorecard_card(round_data, player_initial, player_label):
    """Full detail version of the Rounds History panel's mini scorecard --
    same Hole/Par/Score rows and OUT/IN/TOT/HCP/NET columns, plus Putts,
    Fairway, Net, and Stableford rows underneath, and a Delete/Scrap
    button in the header."""
    round_id = round_data["id"]
    is_live = round_data.get("status") == "in_progress"

    holes_by_number = {h["hole_number"]: h for h in (round_data.get("holes") or [])}
    front9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(1, 10)]
    back9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(10, 19)]

    def _sum_field(hole_subset, field):
        values = [h.get(field) for h in hole_subset if h.get(field) is not None]
        return sum(values) if values else None

    out_par, in_par = _sum_field(front9, "par"), _sum_field(back9, "par")
    tot_par = out_par + in_par if out_par is not None and in_par is not None else None
    out_strokes, in_strokes = _sum_field(front9, "strokes"), _sum_field(back9, "strokes")
    total_strokes = round_data.get("total_strokes")
    out_putts, in_putts = _sum_field(front9, "putts"), _sum_field(back9, "putts")
    tot_putts = out_putts + in_putts if out_putts is not None and in_putts is not None else None
    out_net, in_net = _sum_field(front9, "net_strokes"), _sum_field(back9, "net_strokes")
    tot_net = out_net + in_net if out_net is not None and in_net is not None else None
    out_pts, in_pts = _sum_field(front9, "stableford_points"), _sum_field(back9, "stableford_points")
    total_stableford = round_data.get("total_stableford")

    handicap = round_data.get("handicap")
    hcp_display = format_handicap(handicap)
    net_display = round(total_strokes - handicap) if (handicap is not None and total_strokes is not None) else "—"

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
        + [html.Th("IN"), html.Th("TOT"), html.Th("HCP"), html.Th("NET")]
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
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    score_row = html.Tr(
        className="t3g-history-player-row",
        children=(
            [
                html.Td(
                    html.Div(
                        [
                            html.Div(player_initial, className="t3g-history-player-avatar"),
                            html.Span(player_label),
                        ],
                        className="t3g-history-player-cell",
                    )
                )
            ]
            + _score_cells(front9)
            + [html.Td(out_strokes if out_strokes is not None else "—", className="t3g-history-summary-cell")]
            + _score_cells(back9)
            + [
                html.Td(in_strokes if in_strokes is not None else "—", className="t3g-history-summary-cell"),
                html.Td(total_strokes if total_strokes is not None else "—", className="t3g-history-summary-cell"),
                html.Td(hcp_display, className="t3g-history-summary-cell"),
                html.Td(net_display, className="t3g-history-summary-cell"),
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
                html.Td(""),
                html.Td(""),
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
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    net_row = html.Tr(
        className="t3g-history-detail-row",
        children=(
            [html.Td("Net", className="t3g-history-row-label")]
            + _plain_cells(front9, "net_strokes")
            + [html.Td(out_net if out_net is not None else "—", className="t3g-history-summary-cell")]
            + _plain_cells(back9, "net_strokes")
            + [
                html.Td(in_net if in_net is not None else "—", className="t3g-history-summary-cell"),
                html.Td(tot_net if tot_net is not None else "—", className="t3g-history-summary-cell"),
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    stableford_row = html.Tr(
        className="t3g-history-detail-row",
        children=(
            [html.Td("Stableford", className="t3g-history-row-label")]
            + _plain_cells(front9, "stableford_points")
            + [html.Td(out_pts if out_pts is not None else "—", className="t3g-history-summary-cell")]
            + _plain_cells(back9, "stableford_points")
            + [
                html.Td(in_pts if in_pts is not None else "—", className="t3g-history-summary-cell"),
                html.Td(total_stableford if total_stableford is not None else "—", className="t3g-history-summary-cell"),
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    header_actions = []
    if is_live:
        header_actions.append(live_badge())
    header_actions.append(
        html.Button(
            "Scrap" if is_live else "Delete",
            id={"type": "history-delete-round", "round_id": round_id},
            className="t3g-history-delete-button",
            n_clicks=0,
        )
    )
    header_children = [
        html.Span(round_header_label(round_data), className="t3g-round-card-title"),
        html.Div(header_actions, className="t3g-round-card-header-actions"),
    ]

    return html.Div(
        className="t3g-round-card",
        children=[
            html.Div(header_children, className="t3g-round-card-header"),
            html.Div(
                className="t3g-history-scorecard-wrap",
                children=html.Table(
                    className="t3g-history-scorecard-table",
                    children=[
                        html.Thead([header_row, par_row]),
                        html.Tbody([score_row, putts_row, fairway_row, net_row, stableford_row]),
                    ],
                ),
            ),
        ],
    )


def layout():
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="scoring-history-redirect-signin", refresh=True)

    rounds_resp = requests.get(f"{API_BASE_URL}/rounds/player/{player_id}")
    rounds_history = rounds_resp.json() if rounds_resp.status_code == 200 else []

    player_resp = requests.get(f"{API_BASE_URL}/players/{player_id}")
    player = player_resp.json() if player_resp.status_code == 200 else {}
    player_label = player.get("nickname") or player.get("first_name") or "You"
    player_initial = player_label[0].upper() if player_label else "Y"

    return html.Div(
        className="t3g-page",
        children=[
            dcc.Store(id="scoring-history-store", data=rounds_history),
            dcc.Store(
                id="scoring-history-player-store",
                data={"initial": player_initial, "label": player_label},
            ),
            dcc.Store(id="scoring-history-delete-target"),
            html.Div(
                className="t3g-panel",
                children=[
                    html.Div(
                        className="t3g-panel-navbar",
                        children=html.H3("Scoring History", className="t3g-panel-navbar-title"),
                    ),
                    html.Div(
                        id="scoring-history-list",
                        className="t3g-panel-body",
                    ),
                ],
            ),
            dbc.Modal(
                id="scoring-history-delete-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle(id="scoring-history-delete-modal-title")),
                    dbc.ModalBody(id="scoring-history-delete-modal-body"),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Cancel", id="scoring-history-delete-cancel", color="secondary"),
                            dbc.Button(
                                "Delete",
                                id="scoring-history-delete-confirm",
                                color="danger",
                            ),
                        ]
                    ),
                ],
            ),
        ],
    )


@callback(
    Output("scoring-history-list", "children"),
    Input("scoring-history-store", "data"),
    State("scoring-history-player-store", "data"),
)
def render_rounds(rounds_history, player_info):
    if not rounds_history:
        return html.P("No rounds recorded yet.", className="t3g-empty-state")

    player_info = player_info or {}
    return html.Div(
        # Its own class rather than the home panel's .t3g-rounds-list --
        # that one caps height and scrolls internally to fit a sidebar
        # panel, but this is the whole page, so it should just scroll
        # naturally with everything visible.
        className="t3g-scoring-history-list",
        children=[
            _round_scorecard_card(r, player_info.get("initial", "Y"), player_info.get("label", "You"))
            for r in rounds_history
        ],
    )


@callback(
    Output("scoring-history-delete-modal", "is_open"),
    Output("scoring-history-delete-modal-title", "children"),
    Output("scoring-history-delete-modal-body", "children"),
    Output("scoring-history-delete-target", "data"),
    Input({"type": "history-delete-round", "round_id": ALL}, "n_clicks"),
    Input("scoring-history-delete-cancel", "n_clicks"),
    State("scoring-history-store", "data"),
    prevent_initial_call=True,
)
def toggle_delete_modal(delete_clicks, cancel_clicks, rounds_history):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "scoring-history-delete-cancel":
        return False, dash.no_update, dash.no_update, None

    if isinstance(triggered_id, dict) and triggered_id.get("type") == "history-delete-round":
        if not any(delete_clicks):
            # The set of buttons re-rendering also fires this -- only
            # actually open on a real click.
            raise PreventUpdate

        round_id = triggered_id["round_id"]
        round_data = next((r for r in (rounds_history or []) if r["id"] == round_id), None)
        is_live = bool(round_data and round_data.get("status") == "in_progress")

        if is_live:
            title = "Scrap this round?"
            body = "This live round and every score entered so far will be permanently deleted. This can't be undone."
        else:
            title = "Delete this round?"
            body = "This round and its scorecard will be permanently deleted. This can't be undone."

        return True, title, body, {"round_id": round_id}

    raise PreventUpdate


@callback(
    Output("scoring-history-store", "data"),
    Output("scoring-history-delete-modal", "is_open", allow_duplicate=True),
    Input("scoring-history-delete-confirm", "n_clicks"),
    State("scoring-history-delete-target", "data"),
    State("scoring-history-store", "data"),
    prevent_initial_call=True,
)
def confirm_delete(n_clicks, target, rounds_history):
    if not target or not target.get("round_id"):
        raise PreventUpdate

    response = requests.delete(f"{API_BASE_URL}/rounds/{target['round_id']}")

    if response.status_code not in (204, 404):
        # Leave the list and modal as-is on an unexpected error -- better
        # than silently pretending it worked.
        raise PreventUpdate

    remaining = [r for r in (rounds_history or []) if r["id"] != target["round_id"]]
    return remaining, False