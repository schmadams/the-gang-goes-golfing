# target path: frontend/src/pages/live_round.py (full replacement)
import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from config import API_BASE_URL

dash.register_page(__name__, path="/live-round", name="Live Round")

_MANUAL_FIELD_KEYS = {
    "par": "manual_par",
    "yardage": "manual_yardage",
    "stroke_index": "manual_stroke_index",
}

_FAIRWAY_RADIO_TO_BOOL = {"yes": True, "no": False}
_FAIRWAY_BOOL_TO_RADIO = {True: "yes", False: "no"}


def _hole_par(hole):
    # Manual rounds don't have a real course_holes row yet, so par lives on
    # manual_par instead -- this picks whichever one actually applies.
    return hole.get("manual_par") if hole.get("manual_par") is not None else hole.get("par")


def _hole_value(hole, field, manual_field):
    return hole.get(manual_field) if hole.get(manual_field) is not None else hole.get(field)


def _score_marking_class(strokes, par):
    """
    Traditional scorecard marks, applied around the score itself: birdie ->
    circle, eagle (or better) -> double circle, bogey -> square, double
    bogey (or worse) -> double square. Par or no score yet -> no mark.
    """
    base = "t3g-score-button"
    if strokes is None or par is None:
        return base

    diff = strokes - par
    if diff <= -2:
        return f"{base} t3g-score-eagle"
    if diff == -1:
        return f"{base} t3g-score-birdie"
    if diff == 1:
        return f"{base} t3g-score-bogey"
    if diff >= 2:
        return f"{base} t3g-score-double-bogey"
    return base


def _score_button(hole):
    strokes = hole.get("strokes")
    return html.Button(
        str(strokes) if strokes is not None else "Enter Score",
        id={"type": "live-round-score-button", "hole": hole["hole_number"]},
        className=_score_marking_class(strokes, _hole_par(hole)),
        n_clicks=0,
    )


def _manual_input(field_type, hole, min_val, max_val):
    return dcc.Input(
        id={"type": f"live-round-{field_type}", "hole": hole["hole_number"]},
        type="number",
        min=min_val,
        max=max_val,
        value=hole.get(_MANUAL_FIELD_KEYS[field_type]),
        placeholder="-",
        className="t3g-manual-input",
    )


def _hole_row(hole, is_manual):
    if is_manual:
        yardage_cell = html.Td(_manual_input("yardage", hole, 0, 1000))
        si_cell = html.Td(_manual_input("stroke_index", hole, 1, 18))
        par_cell = html.Td(_manual_input("par", hole, 3, 5))
    else:
        yardage_cell = html.Td(hole["yardage"] if hole.get("yardage") is not None else "-")
        si_cell = html.Td(hole["stroke_index"] if hole.get("stroke_index") is not None else "-")
        par_cell = html.Td(hole["par"] if hole.get("par") is not None else "-")

    return html.Tr(
        [
            html.Td(hole["hole_number"], className="t3g-hole-number"),
            yardage_cell,
            si_cell,
            par_cell,
            html.Td(_score_button(hole)),
        ]
    )


def _score_total_text(holes_subset):
    entered = [h["strokes"] for h in holes_subset if h.get("strokes") is not None]
    return str(sum(entered)) if entered else "-"


def _summary_row(label, holes_subset, score_total_id):
    yardage_total = sum(_hole_value(h, "yardage", "manual_yardage") or 0 for h in holes_subset)
    par_total = sum(_hole_value(h, "par", "manual_par") or 0 for h in holes_subset)
    return html.Tr(
        className="t3g-scorecard-summary-row",
        children=[
            html.Td(label),
            html.Td(yardage_total),
            html.Td("-"),
            html.Td(par_total),
            html.Td(html.Span(_score_total_text(holes_subset), id=score_total_id)),
        ],
    )


def layout():
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="live-round-redirect-signin", refresh=True)

    # No round_id in the URL on purpose -- there can only ever be one
    # in-progress round per player (enforced in the DB), so looking it up
    # by player_id is exactly what lets this page "pick up where you left
    # off" after closing and reopening the app, with no state to restore.
    response = requests.get(f"{API_BASE_URL}/rounds/active/{player_id}")

    if response.status_code != 200:
        return html.Div(
            className="t3g-page",
            children=html.Div(
                className="t3g-panel",
                children=html.Div(
                    className="t3g-panel-body",
                    children=[
                        html.P(
                            "You don't have a live round in progress.",
                            className="t3g-empty-state",
                        ),
                        dcc.Link("Back to home", href="/", className="t3g-link-button"),
                    ],
                ),
            ),
        )

    round_data = response.json()
    is_manual = round_data.get("is_manual")
    holes = round_data.get("holes", [])

    # Single source of truth for every hole's data (score, putts, fairway,
    # par/yardage/SI, whether manual or real) -- everything reactive on
    # this page (score button labels/marks, OUT/IN/TOTAL score sums, the
    # score modal's prefilled values) reads from and writes back to this.
    holes_by_number = {str(h["hole_number"]): h for h in holes}

    front_nine = [h for h in holes if h["hole_number"] <= 9]
    back_nine = [h for h in holes if h["hole_number"] >= 10]

    title_bits = [round_data.get("club_name") or "Live Round"]
    if round_data.get("course_name"):
        title_bits.append(round_data["course_name"])
    if round_data.get("tee_name"):
        title_bits.append(f"{round_data['tee_name']} tees")
    title = " — ".join(title_bits)

    table_rows = (
        [_hole_row(h, is_manual) for h in front_nine]
        + [_summary_row("OUT", front_nine, "live-round-out-total")]
        + [_hole_row(h, is_manual) for h in back_nine]
        + [_summary_row("IN", back_nine, "live-round-in-total")]
        + [_summary_row("TOTAL", holes, "live-round-total-total")]
    )

    return html.Div(
        className="t3g-page",
        children=[
            dcc.Store(id="live-round-id-store", data=round_data["id"]),
            dcc.Store(id="live-round-holes-store", data=holes_by_number),
            dcc.Store(id="live-round-active-hole-store"),
            html.Div(
                className="t3g-panel",
                children=[
                    html.Div(
                        className="t3g-panel-navbar",
                        children=[
                            html.H3(title, className="t3g-panel-navbar-title"),
                            html.Button(
                                "Finish Round",
                                id="live-round-finish-button",
                                className="t3g-panel-action-button",
                            ),
                        ],
                    ),
                    html.Div(
                        className="t3g-panel-body",
                        children=[
                            html.P(
                                "This course isn't in our list yet -- enter yards, stroke "
                                "index, and par for each hole as you go. It'll be saved as a "
                                "real course once you finish the round.",
                                className="t3g-empty-state",
                            )
                            if is_manual
                            else None,
                            html.Table(
                                className="t3g-scorecard-table",
                                children=[
                                    html.Thead(
                                        html.Tr(
                                            [
                                                html.Th("Hole"),
                                                html.Th("Yards"),
                                                html.Th("S.I."),
                                                html.Th("Par"),
                                                html.Th("Score"),
                                            ]
                                        )
                                    ),
                                    html.Tbody(table_rows),
                                ],
                            ),
                            html.Div(id="live-round-error", className="text-danger mt-2"),
                        ],
                    ),
                ],
            ),
            dbc.Modal(
                id="live-round-score-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle(id="live-round-score-modal-title")),
                    dbc.ModalBody(
                        [
                            html.Label("Shots", className="t3g-modal-label"),
                            dbc.Input(
                                id="live-round-score-shots-input",
                                type="number",
                                min=1,
                                max=15,
                                className="mb-3",
                            ),
                            html.Label("Putts", className="t3g-modal-label"),
                            dbc.Input(
                                id="live-round-score-putts-input",
                                type="number",
                                min=0,
                                max=10,
                                className="mb-3",
                            ),
                            html.Label("Fairway Hit", className="t3g-modal-label"),
                            dbc.RadioItems(
                                id="live-round-score-fairway-input",
                                options=[
                                    {"label": "Yes", "value": "yes"},
                                    {"label": "No", "value": "no"},
                                ],
                                inline=True,
                                className="mb-2",
                            ),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Cancel", id="live-round-score-cancel", color="secondary"),
                            dbc.Button("Save", id="live-round-score-save", color="primary"),
                        ]
                    ),
                ],
            ),
            dcc.Location(id="live-round-finish-redirect", refresh=True),
        ],
    )


def _patch_hole(round_id, hole_number, field, value):
    response = requests.patch(
        f"{API_BASE_URL}/rounds/{round_id}/holes/{hole_number}",
        json={field: value},
    )
    if response.status_code == 200:
        return ""
    try:
        return response.json().get("detail", "Couldn't save that value.")
    except ValueError:
        return "Couldn't save that value."


@callback(
    Output("live-round-score-modal", "is_open"),
    Output("live-round-score-modal-title", "children"),
    Output("live-round-score-shots-input", "value"),
    Output("live-round-score-putts-input", "value"),
    Output("live-round-score-fairway-input", "value"),
    Output("live-round-active-hole-store", "data"),
    Input({"type": "live-round-score-button", "hole": ALL}, "n_clicks"),
    Input("live-round-score-cancel", "n_clicks"),
    State("live-round-holes-store", "data"),
    prevent_initial_call=True,
)
def toggle_score_modal(button_clicks, cancel_clicks, holes_by_number):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "live-round-score-cancel":
        return False, dash.no_update, dash.no_update, dash.no_update, dash.no_update, None

    if isinstance(triggered_id, dict) and triggered_id.get("type") == "live-round-score-button":
        if not any(button_clicks):
            # The set of buttons re-rendering also fires this (all
            # n_clicks reset to 0) -- only actually open on a real click.
            raise PreventUpdate

        hole_number = triggered_id["hole"]
        hole = (holes_by_number or {}).get(str(hole_number), {})

        return (
            True,
            f"Hole {hole_number}",
            hole.get("strokes"),
            hole.get("putts"),
            _FAIRWAY_BOOL_TO_RADIO.get(hole.get("fairway_hit")),
            hole_number,
        )

    raise PreventUpdate


@callback(
    Output("live-round-score-modal", "is_open", allow_duplicate=True),
    Output("live-round-holes-store", "data", allow_duplicate=True),
    Output("live-round-error", "children", allow_duplicate=True),
    Input("live-round-score-save", "n_clicks"),
    State("live-round-active-hole-store", "data"),
    State("live-round-id-store", "data"),
    State("live-round-score-shots-input", "value"),
    State("live-round-score-putts-input", "value"),
    State("live-round-score-fairway-input", "value"),
    State("live-round-holes-store", "data"),
    prevent_initial_call=True,
)
def save_score(n_clicks, hole_number, round_id, shots, putts, fairway_radio, holes_by_number):
    if hole_number is None:
        raise PreventUpdate

    fairway_hit = _FAIRWAY_RADIO_TO_BOOL.get(fairway_radio)

    response = requests.patch(
        f"{API_BASE_URL}/rounds/{round_id}/holes/{hole_number}",
        json={"strokes": shots, "putts": putts, "fairway_hit": fairway_hit},
    )

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", "Couldn't save that score.")
        except ValueError:
            detail = "Couldn't save that score."
        return dash.no_update, dash.no_update, detail

    holes_by_number = dict(holes_by_number or {})
    hole = dict(holes_by_number.get(str(hole_number), {}))
    hole.update({"strokes": shots, "putts": putts, "fairway_hit": fairway_hit})
    holes_by_number[str(hole_number)] = hole

    return False, holes_by_number, ""


@callback(
    Output({"type": "live-round-score-button", "hole": ALL}, "children"),
    Output({"type": "live-round-score-button", "hole": ALL}, "className"),
    Output("live-round-out-total", "children"),
    Output("live-round-in-total", "children"),
    Output("live-round-total-total", "children"),
    Input("live-round-holes-store", "data"),
)
def refresh_scorecard(holes_by_number):
    # Fires on load too (no prevent_initial_call) so a resumed round shows
    # correct labels/marks/totals immediately. Recomputes all 18 buttons
    # together (rather than patching just the one hole that changed) since
    # editing a manual round's par can change which mark an already-entered
    # score should carry.
    holes_by_number = holes_by_number or {}

    labels = []
    classes = []
    holes_in_order = []
    for hole_number in range(1, 19):
        hole = holes_by_number.get(str(hole_number), {})
        holes_in_order.append(hole)
        strokes = hole.get("strokes")
        par = _hole_par(hole)
        labels.append(str(strokes) if strokes is not None else "Enter Score")
        classes.append(_score_marking_class(strokes, par))

    out_total = _score_total_text(holes_in_order[:9])
    in_total = _score_total_text(holes_in_order[9:])
    grand_total = _score_total_text(holes_in_order)

    return labels, classes, out_total, in_total, grand_total


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-holes-store", "data", allow_duplicate=True),
    Input({"type": "live-round-par", "hole": ALL}, "value"),
    State("live-round-id-store", "data"),
    State("live-round-holes-store", "data"),
    prevent_initial_call=True,
)
def save_manual_par(values, round_id, holes_by_number):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id:
        raise PreventUpdate
    hole_number = triggered_id["hole"]
    value = dash.ctx.triggered[0]["value"]
    error = _patch_hole(round_id, hole_number, "manual_par", value)

    holes_by_number = dict(holes_by_number or {})
    hole = dict(holes_by_number.get(str(hole_number), {}))
    hole["manual_par"] = value
    holes_by_number[str(hole_number)] = hole

    return error, holes_by_number


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-holes-store", "data", allow_duplicate=True),
    Input({"type": "live-round-yardage", "hole": ALL}, "value"),
    State("live-round-id-store", "data"),
    State("live-round-holes-store", "data"),
    prevent_initial_call=True,
)
def save_manual_yardage(values, round_id, holes_by_number):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id:
        raise PreventUpdate
    hole_number = triggered_id["hole"]
    value = dash.ctx.triggered[0]["value"]
    error = _patch_hole(round_id, hole_number, "manual_yardage", value)

    holes_by_number = dict(holes_by_number or {})
    hole = dict(holes_by_number.get(str(hole_number), {}))
    hole["manual_yardage"] = value
    holes_by_number[str(hole_number)] = hole

    return error, holes_by_number


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-holes-store", "data", allow_duplicate=True),
    Input({"type": "live-round-stroke_index", "hole": ALL}, "value"),
    State("live-round-id-store", "data"),
    State("live-round-holes-store", "data"),
    prevent_initial_call=True,
)
def save_manual_stroke_index(values, round_id, holes_by_number):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id:
        raise PreventUpdate
    hole_number = triggered_id["hole"]
    value = dash.ctx.triggered[0]["value"]
    error = _patch_hole(round_id, hole_number, "manual_stroke_index", value)

    holes_by_number = dict(holes_by_number or {})
    hole = dict(holes_by_number.get(str(hole_number), {}))
    hole["manual_stroke_index"] = value
    holes_by_number[str(hole_number)] = hole

    return error, holes_by_number


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-finish-redirect", "pathname"),
    Input("live-round-finish-button", "n_clicks"),
    State("live-round-id-store", "data"),
    prevent_initial_call=True,
)
def finish_round(n_clicks, round_id):
    response = requests.post(f"{API_BASE_URL}/rounds/{round_id}/finish")

    if response.status_code == 200:
        return "", "/"

    try:
        detail = response.json().get("detail", "Couldn't finish the round.")
    except ValueError:
        detail = "Couldn't finish the round."
    return detail, dash.no_update