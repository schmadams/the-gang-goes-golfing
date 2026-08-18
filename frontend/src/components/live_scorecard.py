# target path: frontend/src/components/live_scorecard.py (new file)
"""
The interactive live-round scorecard -- the merged Hole/Yards/S.I./Par +
per-player Score table, plus everything needed to render one round's full
"page body" (stores, scorecard panel, Enter Score / Scrap Round modals).

Lives here (not in pages/live_round.py, where all of this used to live)
so it can be imported from more than one place without Dash re-running
dash.register_page a second time. pages/*.py modules are auto-discovered
and imported by Dash's own page scanner at startup; importing one page
module directly from another (e.g. tournament.py importing straight from
live_round.py) makes Python execute that module a second time under a
different name, which calls dash.register_page("/live-round") twice and
breaks page routing. Anything under components/ isn't part of that scan,
so it's the safe place for markup that legitimately needs to be shared --
here, so the tournament page's Live Round tab can embed the exact same
scorecard live_round.py's own /live-round page renders, instead of only
ever linking out to it (see tournament.py's _live_round_panel).

live_round.py still owns every @callback for this scorecard (button
clicks, the score modal, Finish/Scrap) -- those have to be registered
exactly once, by Dash's normal import of that one page module, which is
also why several of the plain helper functions below (_hole_par,
_result_badge, _score_marking_class, _score_total_text) are imported back
into live_round.py rather than only used here -- its callbacks need them
too, to keep the score buttons/modal in sync after a save without a full
page reload.
"""
import dash_bootstrap_components as dbc
from dash import dcc, html

_MANUAL_FIELD_KEYS = {
    "par": "manual_par",
    "yardage": "manual_yardage",
    "stroke_index": "manual_stroke_index",
}


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


def render_live_round_body(round_data, player_id):
    """Everything below the (optional) tournament back-navigation --
    stores, the scorecard panel itself, the score/scrap modals -- as a
    flat list of children, not wrapped in a page-level div. Called by both
    live_round.py's own layout() (the standalone /live-round page) and
    tournament.py's _live_round_panel (embedded straight into the
    tournament page's Live Round tab) -- "just show the scorecard of the
    round that is underway" only works if the markup itself is reusable
    like this, not just the page it lives on. Every id in here is a fixed
    singleton (not round-scoped), which is safe because a player can only
    ever have one round -- casual or tournament -- actually rendering this
    at a time; see the tournament_scope split in backend/services/
    rounds.py for why that's also true for tournament rounds specifically,
    not just casual ones."""
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
    # status check matters now that a round can be reached directly by id
    # (a stale Continue Live Round link after the round's already been
    # finished, say) rather than only ever through the "my one active
    # round" lookup, which by construction could never return anything
    # but an in_progress round.
    if is_owner_of_round and round_data.get("status") == "in_progress":
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

    return [
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
    ]