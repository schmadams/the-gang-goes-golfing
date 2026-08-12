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


def _player_display_name(player):
    return (
        player.get("nickname")
        or f"{player.get('first_name', '')} {player.get('surname', '')}".strip()
        or "Player"
    )


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


def _score_button(player_id, hole, par):
    strokes = hole.get("strokes")
    return html.Button(
        str(strokes) if strokes is not None else "Enter Score",
        id={"type": "live-round-score-button", "player": player_id, "hole": hole["hole_number"]},
        className=_score_marking_class(strokes, par),
        n_clicks=0,
    )


def _manual_input(owner_id, field_type, hole, min_val, max_val):
    return dcc.Input(
        id={"type": f"live-round-{field_type}", "hole": hole["hole_number"]},
        type="number",
        min=min_val,
        max=max_val,
        value=hole.get(_MANUAL_FIELD_KEYS[field_type]),
        placeholder="-",
        className="t3g-manual-input",
    )


def _score_total_text(holes_subset):
    entered = [h["strokes"] for h in holes_subset if h.get("strokes") is not None]
    return str(sum(entered)) if entered else "-"


def _summary_row(label, hole_numbers, reference_holes, players, holes_by_player, total_id_type):
    """OUT/IN/TOTAL row for the merged table -- yardage/par totals are
    shared (same course info for everyone), but the score total is
    per-player, one cell per player in the same column order as the rest
    of the table."""
    ref_subset = [reference_holes.get(n, {"hole_number": n}) for n in hole_numbers]
    yardage_total = sum(_hole_value(h, "yardage", "manual_yardage") or 0 for h in ref_subset)
    par_total = sum(_hole_par(h) or 0 for h in ref_subset)

    score_cells = [
        html.Td(
            html.Span(
                _score_total_text([holes_by_player[p["player_id"]].get(n, {}) for n in hole_numbers]),
                id={"type": total_id_type, "player": p["player_id"]},
            )
        )
        for p in players
    ]

    return html.Tr(
        className="t3g-scorecard-summary-row",
        children=[html.Td(label), html.Td(yardage_total), html.Td("-"), html.Td(par_total), *score_cells],
    )


def _scorecard_table(players, owner_player, is_manual, is_owner_of_round):
    """One shared scorecard table for the whole group, instead of a
    separate full scorecard stacked per player -- Hole/Yards/S.I./Par are
    common to everyone (sourced from the round owner's holes, since that's
    the only place manual-round course info ever gets entered -- other
    players' own rows never carry it), with one Score column per player
    under a shared "Score" header so the group can compare a hole at a
    glance instead of scrolling through separate cards."""
    reference_player = owner_player or players[0]
    reference_holes = {h["hole_number"]: h for h in reference_player["holes"]}
    holes_by_player = {p["player_id"]: {h["hole_number"]: h for h in p["holes"]} for p in players}
    can_edit_course_info = is_owner_of_round

    header = html.Thead(
        [
            html.Tr(
                [
                    html.Th("Hole", rowSpan=2),
                    html.Th("Yards", rowSpan=2),
                    html.Th("S.I.", rowSpan=2),
                    html.Th("Par", rowSpan=2),
                    html.Th("Score", colSpan=len(players), className="t3g-scorecard-score-group"),
                ]
            ),
            html.Tr(
                [
                    html.Th(
                        _player_display_name(p) + (" (you)" if p.get("is_viewer") else ""),
                        className="t3g-scorecard-player-col",
                    )
                    for p in players
                ]
            ),
        ]
    )

    def _score_row(hole_number):
        ref_hole = reference_holes.get(hole_number, {"hole_number": hole_number})

        if is_manual and can_edit_course_info:
            yardage_cell = html.Td(_manual_input(None, "yardage", ref_hole, 0, 1000))
            si_cell = html.Td(_manual_input(None, "stroke_index", ref_hole, 1, 18))
            par_cell = html.Td(_manual_input(None, "par", ref_hole, 3, 5))
        else:
            yardage_val = _hole_value(ref_hole, "yardage", "manual_yardage")
            si_val = _hole_value(ref_hole, "stroke_index", "manual_stroke_index")
            par_val = _hole_par(ref_hole)
            yardage_cell = html.Td(yardage_val if yardage_val is not None else "-")
            si_cell = html.Td(si_val if si_val is not None else "-")
            par_cell = html.Td(par_val if par_val is not None else "-")

        par = _hole_par(ref_hole)
        score_cells = [
            html.Td(
                _score_button(
                    p["player_id"],
                    holes_by_player[p["player_id"]].get(hole_number, {"hole_number": hole_number}),
                    par,
                )
            )
            for p in players
        ]

        return html.Tr(
            [html.Td(hole_number, className="t3g-hole-number"), yardage_cell, si_cell, par_cell, *score_cells]
        )

    front_nine_rows = [_score_row(n) for n in range(1, 10)]
    back_nine_rows = [_score_row(n) for n in range(10, 19)]

    body = html.Tbody(
        front_nine_rows
        + [_summary_row("OUT", range(1, 10), reference_holes, players, holes_by_player, "live-round-out-total")]
        + back_nine_rows
        + [_summary_row("IN", range(10, 19), reference_holes, players, holes_by_player, "live-round-in-total")]
        + [_summary_row("TOTAL", range(1, 19), reference_holes, players, holes_by_player, "live-round-total-total")]
    )

    return html.Div(
        className="t3g-player-scorecard",
        children=html.Table(className="t3g-scorecard-table t3g-scorecard-table--multi", children=[header, body]),
    )


def layout(**kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="live-round-redirect-signin", refresh=True)

    # No round_id in the URL on purpose -- there can only ever be one round
    # (owned or joined) a player is an accepted participant in at a time
    # (enforced app-side in backend/services/rounds.py), so looking it up
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
    is_owner_of_round = bool(round_data.get("is_owner"))
    players = round_data.get("players", [])
    pending_invites = round_data.get("pending_invites", [])
    owner_player = next((p for p in players if p.get("is_owner")), None)

    for p in players:
        p["is_viewer"] = p["player_id"] == player_id

    # Single source of truth for every player's hole data -- everything
    # reactive on this page (score button labels/marks, OUT/IN/TOTAL score
    # sums per player, the score modal's prefilled values) reads from and
    # writes back to this. A list (not a dict keyed by player_id) so
    # iteration order is unambiguous and stays identical between this
    # layout and the refresh_scorecard callback that rebuilds it.
    players_store_data = [
        {
            "player_id": p["player_id"],
            "display_name": _player_display_name(p),
            "is_owner": p.get("is_owner", False),
            "holes": {str(h["hole_number"]): h for h in p["holes"]},
        }
        for p in players
    ]

    title_bits = [round_data.get("club_name") or "Live Round"]
    if round_data.get("course_name"):
        title_bits.append(round_data["course_name"])
    if round_data.get("tee_name"):
        title_bits.append(f"{round_data['tee_name']} tees")
    title = " — ".join(title_bits)

    header_actions = []
    if is_owner_of_round:
        header_actions = [
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
        ]

    pending_note = None
    if pending_invites:
        names = ", ".join(_player_display_name(p) for p in pending_invites)
        pending_note = html.P(f"Waiting on {names} to accept their invite.", className="t3g-empty-state")

    return html.Div(
        className="t3g-page",
        children=[
            dcc.Store(id="live-round-id-store", data=round_data["id"]),
            dcc.Store(id="live-round-owner-id-store", data=owner_player["player_id"] if owner_player else None),
            dcc.Store(id="live-round-players-store", data=players_store_data),
            dcc.Store(id="live-round-active-hole-store"),
            html.Div(
                className="t3g-panel",
                children=[
                    html.Div(
                        className="t3g-panel-navbar",
                        children=[
                            html.H3(title, className="t3g-panel-navbar-title"),
                            html.Div(header_actions, className="t3g-panel-navbar-action")
                            if header_actions
                            else None,
                        ],
                    ),
                    html.Div(
                        className="t3g-panel-body",
                        children=[
                            html.P(
                                "This course isn't in our list yet -- the round owner enters yards, "
                                "stroke index, and par for each hole as they go. It'll be saved as a "
                                "real course once the round finishes.",
                                className="t3g-empty-state",
                            )
                            if is_manual
                            else None,
                            pending_note,
                            _scorecard_table(players, owner_player, is_manual, is_owner_of_round)
                            if players
                            else None,
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
                        "This live round and every score entered so far -- for every player in it -- "
                        "will be permanently deleted. This can't be undone."
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


def _patch_hole(round_id, player_id, hole_number, field, value):
    response = requests.patch(
        f"{API_BASE_URL}/rounds/{round_id}/players/{player_id}/holes/{hole_number}",
        json={field: value},
    )
    if response.status_code == 200:
        return ""
    try:
        return response.json().get("detail", "Couldn't save that value.")
    except ValueError:
        return "Couldn't save that value."


def _find_player(players, player_id):
    return next((p for p in (players or []) if p["player_id"] == player_id), None)


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
    Input({"type": "live-round-score-button", "player": ALL, "hole": ALL}, "n_clicks"),
    Input("live-round-score-cancel", "n_clicks"),
    State("live-round-players-store", "data"),
    prevent_initial_call=True,
)
def toggle_score_modal(button_clicks, cancel_clicks, players):
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
        clicked_player_id = triggered_id["player"]
        player = _find_player(players, clicked_player_id) or {}
        hole = (player.get("holes") or {}).get(str(hole_number), {})
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

        title = f"{player.get('display_name', 'Player')} · Hole {hole_number}"
        if par is not None:
            title += f" · Par {par}"

        return (
            True,
            title,
            par,
            strokes,
            str(strokes) if strokes is not None else "-",
            putts,
            str(putts) if putts is not None else "-",
            _FAIRWAY_BOOL_TO_RADIO.get(hole.get("fairway_hit")),
            fairway_row_style,
            badge_text,
            badge_class,
            {"player_id": clicked_player_id, "hole_number": hole_number},
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
    Output("live-round-players-store", "data", allow_duplicate=True),
    Output("live-round-error", "children", allow_duplicate=True),
    Input("live-round-score-save", "n_clicks"),
    State("live-round-active-hole-store", "data"),
    State("live-round-id-store", "data"),
    State("live-round-score-shots-store", "data"),
    State("live-round-score-putts-store", "data"),
    State("live-round-score-fairway-input", "value"),
    State("live-round-score-modal-par-store", "data"),
    State("live-round-players-store", "data"),
    prevent_initial_call=True,
)
def save_score(n_clicks, active_hole, round_id, shots, putts, fairway_radio, par, players):
    if not active_hole:
        raise PreventUpdate

    target_player_id = active_hole["player_id"]
    hole_number = active_hole["hole_number"]

    # Par 3s don't get a fairway hit toggle in the UI -- also ignore
    # whatever's in the radio's stale value here, rather than trusting a
    # hidden control not to leak a selection into the save.
    fairway_hit = None if par == 3 else _FAIRWAY_RADIO_TO_BOOL.get(fairway_radio)

    response = requests.patch(
        f"{API_BASE_URL}/rounds/{round_id}/players/{target_player_id}/holes/{hole_number}",
        json={"strokes": shots, "putts": putts, "fairway_hit": fairway_hit},
    )

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", "Couldn't save that score.")
        except ValueError:
            detail = "Couldn't save that score."
        return dash.no_update, dash.no_update, detail

    players = [dict(p) for p in (players or [])]
    for p in players:
        if p["player_id"] == target_player_id:
            holes = dict(p["holes"])
            hole = dict(holes.get(str(hole_number), {}))
            hole.update({"strokes": shots, "putts": putts, "fairway_hit": fairway_hit})
            holes[str(hole_number)] = hole
            p["holes"] = holes

    return False, players, ""


@callback(
    Output({"type": "live-round-score-button", "player": ALL, "hole": ALL}, "children"),
    Output({"type": "live-round-score-button", "player": ALL, "hole": ALL}, "className"),
    Output({"type": "live-round-out-total", "player": ALL}, "children"),
    Output({"type": "live-round-in-total", "player": ALL}, "children"),
    Output({"type": "live-round-total-total", "player": ALL}, "children"),
    Input("live-round-players-store", "data"),
)
def refresh_scorecard(players):
    # Fires on load too (no prevent_initial_call) so a resumed round shows
    # correct labels/marks/totals immediately. The merged table lays out
    # DOM-order hole-major (one row per hole, one score button per player
    # in it), so the label/class lists below have to be built the same
    # way -- hole outer, player inner -- for Dash to pair this flat list
    # back up to the right button for each (hole, player).
    players = players or []

    owner = next((p for p in players if p.get("is_owner")), None)
    reference_holes = (owner or (players[0] if players else {})).get("holes") or {}

    labels, classes = [], []
    for hole_number in range(1, 19):
        ref_hole = reference_holes.get(str(hole_number), {})
        par = _hole_par(ref_hole)
        for player in players:
            hole = (player.get("holes") or {}).get(str(hole_number), {})
            strokes = hole.get("strokes")
            labels.append(str(strokes) if strokes is not None else "Enter Score")
            classes.append(_score_marking_class(strokes, par))

    out_totals, in_totals, total_totals = [], [], []
    for player in players:
        holes_by_number = player.get("holes") or {}
        holes_in_order = [holes_by_number.get(str(n), {}) for n in range(1, 19)]
        out_totals.append(_score_total_text(holes_in_order[:9]))
        in_totals.append(_score_total_text(holes_in_order[9:]))
        total_totals.append(_score_total_text(holes_in_order))

    return labels, classes, out_totals, in_totals, total_totals


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-players-store", "data", allow_duplicate=True),
    Input({"type": "live-round-par", "hole": ALL}, "value"),
    State("live-round-id-store", "data"),
    State("live-round-owner-id-store", "data"),
    State("live-round-players-store", "data"),
    prevent_initial_call=True,
)
def save_manual_par(values, round_id, owner_id, players):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id:
        raise PreventUpdate
    hole_number = triggered_id["hole"]
    value = dash.ctx.triggered[0]["value"]
    error = _patch_hole(round_id, owner_id, hole_number, "manual_par", value)

    players = [dict(p) for p in (players or [])]
    for p in players:
        if p["player_id"] == owner_id:
            holes = dict(p["holes"])
            hole = dict(holes.get(str(hole_number), {}))
            hole["manual_par"] = value
            holes[str(hole_number)] = hole
            p["holes"] = holes

    return error, players


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-players-store", "data", allow_duplicate=True),
    Input({"type": "live-round-yardage", "hole": ALL}, "value"),
    State("live-round-id-store", "data"),
    State("live-round-owner-id-store", "data"),
    State("live-round-players-store", "data"),
    prevent_initial_call=True,
)
def save_manual_yardage(values, round_id, owner_id, players):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id:
        raise PreventUpdate
    hole_number = triggered_id["hole"]
    value = dash.ctx.triggered[0]["value"]
    error = _patch_hole(round_id, owner_id, hole_number, "manual_yardage", value)

    players = [dict(p) for p in (players or [])]
    for p in players:
        if p["player_id"] == owner_id:
            holes = dict(p["holes"])
            hole = dict(holes.get(str(hole_number), {}))
            hole["manual_yardage"] = value
            holes[str(hole_number)] = hole
            p["holes"] = holes

    return error, players


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-players-store", "data", allow_duplicate=True),
    Input({"type": "live-round-stroke_index", "hole": ALL}, "value"),
    State("live-round-id-store", "data"),
    State("live-round-owner-id-store", "data"),
    State("live-round-players-store", "data"),
    prevent_initial_call=True,
)
def save_manual_stroke_index(values, round_id, owner_id, players):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id:
        raise PreventUpdate
    hole_number = triggered_id["hole"]
    value = dash.ctx.triggered[0]["value"]
    error = _patch_hole(round_id, owner_id, hole_number, "manual_stroke_index", value)

    players = [dict(p) for p in (players or [])]
    for p in players:
        if p["player_id"] == owner_id:
            holes = dict(p["holes"])
            hole = dict(holes.get(str(hole_number), {}))
            hole["manual_stroke_index"] = value
            holes[str(hole_number)] = hole
            p["holes"] = holes

    return error, players


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