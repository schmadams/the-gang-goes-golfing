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
    bogey (or worse) -> double square. No score yet -> the wider "Enter
    Score" text button, not a shape.

    Once a score exists, the button switches to a fixed-size square
    (t3g-score-button-filled) regardless of the mark -- a mark drawn with
    border-radius/box-shadow only looks like a true circle or square when
    the box itself is a square, and this button's width otherwise varies
    with its label ("Enter Score" vs "3").
    """
    if strokes is None:
        return "t3g-score-button"

    base = "t3g-score-button t3g-score-button-filled"
    if par is None:
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


def _result_badge(strokes, par):
    """Live text feedback in the Enter Score modal -- same par-vs-strokes
    logic as _score_marking_class, but as a word instead of a shape, since
    there's no fixed-shape score box in the modal to draw a mark around."""
    if strokes is None or par is None:
        return "", "t3g-result-badge"

    diff = strokes - par
    if diff <= -2:
        return "Eagle or better", "t3g-result-badge t3g-result-eagle"
    if diff == -1:
        return "Birdie", "t3g-result-badge t3g-result-birdie"
    if diff == 0:
        return "Par", "t3g-result-badge t3g-result-par"
    if diff == 1:
        return "Bogey", "t3g-result-badge t3g-result-bogey"
    return "Double bogey or worse", "t3g-result-badge t3g-result-double-bogey"


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
                            html.Div(
                                className="t3g-panel-navbar-action",
                                children=[
                                    html.Button(
                                        "Scrap Round",
                                        id="live-round-scrap-button",
                                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                                    ),
                                    html.Button(
                                        "Finish Round",
                                        id="live-round-finish-button",
                                        className="t3g-panel-action-button",
                                    ),
                                ],
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
                className="t3g-score-modal",
                children=[
                    dbc.ModalHeader(dbc.ModalTitle(id="live-round-score-modal-title")),
                    dbc.ModalBody(
                        [
                            html.Div(
                                className="t3g-stepper-row",
                                children=[
                                    html.Div(
                                        className="t3g-stepper-col",
                                        children=[
                                            html.Div("Score", className="t3g-stepper-label"),
                                            html.Div(
                                                className="t3g-stepper",
                                                children=[
                                                    html.Button(
                                                        "+",
                                                        id="live-round-score-shots-plus",
                                                        className="t3g-stepper-button",
                                                        n_clicks=0,
                                                    ),
                                                    html.Div(
                                                        "-",
                                                        id="live-round-score-shots-display",
                                                        className="t3g-stepper-value",
                                                    ),
                                                    html.Button(
                                                        "–",
                                                        id="live-round-score-shots-minus",
                                                        className="t3g-stepper-button",
                                                        n_clicks=0,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        className="t3g-stepper-col",
                                        children=[
                                            html.Div("Putts", className="t3g-stepper-label"),
                                            html.Div(
                                                className="t3g-stepper",
                                                children=[
                                                    html.Button(
                                                        "+",
                                                        id="live-round-score-putts-plus",
                                                        className="t3g-stepper-button",
                                                        n_clicks=0,
                                                    ),
                                                    html.Div(
                                                        "-",
                                                        id="live-round-score-putts-display",
                                                        className="t3g-stepper-value",
                                                    ),
                                                    html.Button(
                                                        "–",
                                                        id="live-round-score-putts-minus",
                                                        className="t3g-stepper-button",
                                                        n_clicks=0,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            html.Div(id="live-round-score-result-badge", className="t3g-result-badge"),
                            html.Div(
                                id="live-round-score-fairway-row",
                                className="t3g-fairway-row",
                                children=[
                                    html.Label("Fairway Hit", className="t3g-fairway-label"),
                                    dbc.RadioItems(
                                        id="live-round-score-fairway-input",
                                        options=[
                                            {"label": "Yes", "value": "yes"},
                                            {"label": "No", "value": "no"},
                                        ],
                                        inline=True,
                                        className="t3g-fairway-toggle",
                                    ),
                                ],
                            ),
                            dcc.Store(id="live-round-score-shots-store"),
                            dcc.Store(id="live-round-score-putts-store"),
                            dcc.Store(id="live-round-score-modal-par-store"),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Cancel", id="live-round-score-cancel", color="secondary"),
                            dbc.Button(
                                "Enter",
                                id="live-round-score-save",
                                color="primary",
                                className="t3g-enter-button",
                            ),
                        ]
                    ),
                ],
            ),
            dbc.Modal(
                id="live-round-scrap-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Scrap this round?")),
                    dbc.ModalBody(
                        "This live round and every score entered so far will be permanently "
                        "deleted. This can't be undone."
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Cancel", id="live-round-scrap-cancel", color="secondary"),
                            dbc.Button(
                                "Scrap Round",
                                id="live-round-scrap-confirm",
                                color="danger",
                            ),
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
    Output("live-round-score-modal-par-store", "data"),
    Output("live-round-score-shots-store", "data"),
    Output("live-round-score-shots-display", "children"),
    Output("live-round-score-putts-store", "data"),
    Output("live-round-score-putts-display", "children"),
    Output("live-round-score-fairway-input", "value"),
    Output("live-round-score-fairway-row", "style"),
    Output("live-round-score-result-badge", "children"),
    Output("live-round-score-result-badge", "className"),
    Output("live-round-active-hole-store", "data"),
    Input({"type": "live-round-score-button", "hole": ALL}, "n_clicks"),
    Input("live-round-score-cancel", "n_clicks"),
    State("live-round-holes-store", "data"),
    prevent_initial_call=True,
)
def toggle_score_modal(button_clicks, cancel_clicks, holes_by_number):
    triggered_id = dash.ctx.triggered_id
    no_update = dash.no_update

    if triggered_id == "live-round-score-cancel":
        return (
            False, no_update, no_update, no_update, no_update,
            no_update, no_update, no_update, no_update, no_update, no_update, None,
        )

    if isinstance(triggered_id, dict) and triggered_id.get("type") == "live-round-score-button":
        if not any(button_clicks):
            # The set of buttons re-rendering also fires this (all
            # n_clicks reset to 0) -- only actually open on a real click.
            raise PreventUpdate

        hole_number = triggered_id["hole"]
        hole = (holes_by_number or {}).get(str(hole_number), {})
        par = _hole_par(hole)
        strokes = hole.get("strokes")
        putts = hole.get("putts")

        # Nothing entered for this hole yet -- start the steppers at a
        # sensible guess (par, 2 putts) instead of blank, so most holes are
        # just a tap or two away instead of building the number from zero.
        # A hole that's already been scored always shows its real values.
        if strokes is None and par is not None:
            strokes = par
        if putts is None:
            putts = 2

        badge_text, badge_class = _result_badge(strokes, par)

        # Fairway hit isn't a meaningful stat on a par 3 -- there's
        # normally no lay-up shot to a fairway, you're going straight at
        # the green -- so hide the toggle rather than ask a question that
        # doesn't apply.
        fairway_row_style = {"display": "none"} if par == 3 else {}

        return (
            True,
            f"Hole {hole_number} · Par {par}" if par is not None else f"Hole {hole_number}",
            par,
            strokes,
            str(strokes) if strokes is not None else "-",
            putts,
            str(putts) if putts is not None else "-",
            _FAIRWAY_BOOL_TO_RADIO.get(hole.get("fairway_hit")),
            fairway_row_style,
            badge_text,
            badge_class,
            hole_number,
        )

    raise PreventUpdate


@callback(
    Output("live-round-score-shots-store", "data", allow_duplicate=True),
    Output("live-round-score-shots-display", "children", allow_duplicate=True),
    Output("live-round-score-result-badge", "children", allow_duplicate=True),
    Output("live-round-score-result-badge", "className", allow_duplicate=True),
    Input("live-round-score-shots-plus", "n_clicks"),
    Input("live-round-score-shots-minus", "n_clicks"),
    State("live-round-score-shots-store", "data"),
    State("live-round-score-modal-par-store", "data"),
    prevent_initial_call=True,
)
def adjust_shots(plus_clicks, minus_clicks, current, par):
    triggered_id = dash.ctx.triggered_id
    value = current

    if triggered_id == "live-round-score-shots-plus":
        value = 1 if value is None else min(value + 1, 15)
    elif triggered_id == "live-round-score-shots-minus" and value is not None:
        value = value - 1 if value > 1 else None

    display = str(value) if value is not None else "-"
    badge_text, badge_class = _result_badge(value, par)
    return value, display, badge_text, badge_class


@callback(
    Output("live-round-score-putts-store", "data", allow_duplicate=True),
    Output("live-round-score-putts-display", "children", allow_duplicate=True),
    Input("live-round-score-putts-plus", "n_clicks"),
    Input("live-round-score-putts-minus", "n_clicks"),
    State("live-round-score-putts-store", "data"),
    prevent_initial_call=True,
)
def adjust_putts(plus_clicks, minus_clicks, current):
    triggered_id = dash.ctx.triggered_id
    value = current

    if triggered_id == "live-round-score-putts-plus":
        value = 0 if value is None else min(value + 1, 10)
    elif triggered_id == "live-round-score-putts-minus" and value is not None:
        value = value - 1 if value > 0 else None

    return value, str(value) if value is not None else "-"


@callback(
    Output("live-round-score-modal", "is_open", allow_duplicate=True),
    Output("live-round-holes-store", "data", allow_duplicate=True),
    Output("live-round-error", "children", allow_duplicate=True),
    Input("live-round-score-save", "n_clicks"),
    State("live-round-active-hole-store", "data"),
    State("live-round-id-store", "data"),
    State("live-round-score-shots-store", "data"),
    State("live-round-score-putts-store", "data"),
    State("live-round-score-fairway-input", "value"),
    State("live-round-score-modal-par-store", "data"),
    State("live-round-holes-store", "data"),
    prevent_initial_call=True,
)
def save_score(n_clicks, hole_number, round_id, shots, putts, fairway_radio, par, holes_by_number):
    if hole_number is None:
        raise PreventUpdate

    # Par 3s don't get a fairway hit toggle in the UI -- also ignore
    # whatever's in the radio's stale value here, rather than trusting a
    # hidden control not to leak a selection into the save.
    fairway_hit = None if par == 3 else _FAIRWAY_RADIO_TO_BOOL.get(fairway_radio)

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


@callback(
    Output("live-round-scrap-modal", "is_open"),
    Input("live-round-scrap-button", "n_clicks"),
    Input("live-round-scrap-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_scrap_modal(open_clicks, cancel_clicks):
    return dash.ctx.triggered_id == "live-round-scrap-button"


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-finish-redirect", "pathname", allow_duplicate=True),
    Output("live-round-scrap-modal", "is_open", allow_duplicate=True),
    Input("live-round-scrap-confirm", "n_clicks"),
    State("live-round-id-store", "data"),
    prevent_initial_call=True,
)
def confirm_scrap_round(n_clicks, round_id):
    response = requests.delete(f"{API_BASE_URL}/rounds/{round_id}")

    if response.status_code in (204, 404):
        # 404 just means it's already gone somehow -- either way there's
        # nothing left to scrap, so send them home rather than showing an
        # error for a round that no longer exists.
        return "", "/", False

    try:
        detail = response.json().get("detail", "Couldn't scrap the round.")
    except ValueError:
        detail = "Couldn't scrap the round."
    return detail, dash.no_update, False