# target path: frontend/src/components/live_scorecard.py (full replacement)
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


def _score_marking_class(strokes, par, nr=False):
    """
    Traditional scorecard marks, applied around the score itself: birdie ->
    circle, eagle (or better) -> double circle, bogey -> square, double
    bogey (or worse) -> double square. No score yet -> the wider "Enter
    Score" text button, not a shape. nr (No Return, tournament rounds
    only -- see mark_round_no_result/HoleScoreUpdate.nr) always wins over
    any of that regardless of whether a stale strokes value happens to
    still be sitting on the hole -- an NR'd hole never actually has one in
    practice (the modal's "NR" save and the bulk NR Round action both
    clear strokes at the same time they set nr), but this keeps the mark
    correct even in that edge case rather than silently falling through to
    a birdie/bogey mark that shouldn't apply anymore.

    Once a score exists, the button switches to a fixed-size square
    (t3g-score-button-filled) regardless of the mark -- a mark drawn with
    border-radius/box-shadow only looks like a true circle or square when
    the box itself is a square, and this button's width otherwise varies
    with its label ("Enter Score" vs "3" vs "NR").
    """
    if nr:
        return "t3g-score-button t3g-score-button-filled t3g-score-nr"
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
    nr = bool(hole.get("nr"))
    return html.Button(
        "NR" if nr else (str(strokes) if strokes is not None else "Enter Score"),
        id={"type": "live-round-score-button", "player": player_id, "hole": hole["hole_number"]},
        className=_score_marking_class(strokes, par, nr),
        n_clicks=0,
    )


def _holeview_score_button(player_id, hole, par):
    """Same button, same marking classes, as _score_button above -- just a
    distinct id "type" (live-round-holeview-score-button vs
    live-round-score-button). The Hole by Hole view only ever renders one
    hole's worth of these at a time (see _hole_by_hole_panel_content,
    rebuilt whole by render_holeview_panel in live_round.py whenever the
    active hole or the players store changes), but that hole is also
    always present in the Full Scorecard view's own always-mounted table
    -- reusing the same id type for both would mean two components on the
    page sharing the literal same id (same player, same hole) the moment
    both views exist in the DOM at once, which Dash doesn't allow. Kept
    working with the exact same Enter Score modal regardless -- see
    toggle_score_modal in live_round.py, whose Input list matches on
    either type."""
    strokes = hole.get("strokes")
    nr = bool(hole.get("nr"))
    return html.Button(
        "NR" if nr else (str(strokes) if strokes is not None else "Enter Score"),
        id={"type": "live-round-holeview-score-button", "player": player_id, "hole": hole["hole_number"]},
        className=_score_marking_class(strokes, par, nr),
        n_clicks=0,
    )


def _initials(name):
    words = (name or "").split()
    letters = "".join(w[0] for w in words[:2] if w)
    return letters.upper() or "?"


def _to_par_text(value):
    if value is None:
        return "–"
    if value == 0:
        return "E"
    return f"+{value}" if value > 0 else str(value)


def _player_round_to_par(holes_by_number, reference_holes):
    """Running score-to-par across however many holes have a strokes value
    entered so far -- not just a completed front/back 9, all of them, the
    same "sum whatever's actually there" idea _score_total_text already
    uses for the OUT/IN/TOTAL row, just relative to par per hole instead
    of a raw stroke count. holes_by_number/reference_holes are both the
    store-shape dict keyed by *string* hole number."""
    total_diff = 0
    any_entered = False
    for hole_number_str, hole in (holes_by_number or {}).items():
        strokes = hole.get("strokes")
        if strokes is None:
            continue
        par = _hole_par(reference_holes.get(hole_number_str, {}))
        if par is None:
            continue
        total_diff += strokes - par
        any_entered = True
    return total_diff if any_entered else None


def _first_unscored_hole(players):
    """First hole (1-18) missing a strokes value for any accepted player,
    across the store-shape players list -- picking up scoring exactly
    where a group left off is a much more useful Hole by Hole starting
    point than always opening on Hole 1 partway through a round. Falls
    back to 18 once every hole for every player already has a score.

    A hole marked nr (No Return) counts as resolved here even though it
    has no strokes value -- there's nothing left to enter for it, same as
    a hole with a real score; without this check, a group with an NR'd
    hole would never advance past it on page load, or in save_score's
    Hole by Hole auto-advance in live_round.py, which uses the same
    condition for the same reason."""
    for hole_number in range(1, 19):
        for p in players:
            hole = (p.get("holes") or {}).get(str(hole_number), {})
            if hole.get("strokes") is None and not hole.get("nr"):
                return hole_number
    return 18


def _hole_by_hole_panel_content(hole_number, players):
    """The Hole by Hole view's actual content -- nav header (prev/next,
    par/yards/S.I. for this one hole) plus one row per player (avatar,
    name, running to-par, score button). Takes the same store-shape
    players list live-round-players-store holds (holes keyed by *string*
    hole number) so this one function works identically whether it's
    called once at initial page render (render_live_round_body, below) or
    rebuilt later by render_holeview_panel in live_round.py after a nav
    click or a saved score -- there's only ever one shape of players data
    to reason about, not two.

    Manual-round course info (par/yardage/S.I. for a course that isn't in
    the local list yet) stays editable only from the Full Scorecard view
    for now -- duplicating those editable cells here, kept in sync with
    the same save_manual_* callbacks, is more than this view needs to
    pull its weight; a plain read-only note points back to where to enter
    it instead."""
    owner = next((p for p in players if p.get("is_owner")), None)
    reference_holes = (owner or (players[0] if players else {})).get("holes") or {}
    ref_hole = reference_holes.get(str(hole_number), {"hole_number": hole_number})
    par = _hole_par(ref_hole)
    yardage = _hole_value(ref_hole, "yardage", "manual_yardage")
    stroke_index = _hole_value(ref_hole, "stroke_index", "manual_stroke_index")

    par_text = str(par) if par is not None else "–"
    yardage_text = f"{yardage} yds" if yardage is not None else "– yds"
    si_text = f"S.I. {stroke_index}" if stroke_index is not None else "S.I. –"

    header = html.Div(
        className="t3g-holeview-header",
        children=[
            html.Button(
                "‹", id="live-round-holeview-prev", className="t3g-holeview-nav-button", n_clicks=0
            ),
            html.Div(
                className="t3g-holeview-hole-info",
                children=[
                    html.Div(f"Hole {hole_number}", className="t3g-holeview-hole-title"),
                    html.Div(
                        f"Par {par_text} · {yardage_text} · {si_text}",
                        className="t3g-holeview-hole-meta",
                    ),
                ],
            ),
            html.Button(
                "›", id="live-round-holeview-next", className="t3g-holeview-nav-button", n_clicks=0
            ),
        ],
    )

    rows = [
        html.Div(
            className="t3g-holeview-player-row",
            children=[
                html.Span(_initials(p["display_name"]), className="t3g-holeview-player-avatar"),
                html.Div(
                    className="t3g-holeview-player-info",
                    children=[
                        html.Div(
                            p["display_name"] + (" (you)" if p.get("is_viewer") else ""),
                            className="t3g-holeview-player-name",
                        ),
                        html.Div(
                            _to_par_text(_player_round_to_par(p.get("holes") or {}, reference_holes)),
                            className="t3g-holeview-player-total",
                        ),
                    ],
                ),
                _holeview_score_button(
                    p["player_id"],
                    (p.get("holes") or {}).get(str(hole_number), {"hole_number": hole_number}),
                    par,
                ),
            ],
        )
        for p in players
    ]

    return [header, html.Div(rows, className="t3g-holeview-rows")]


def _scorecard_view_toggle(active_view):
    """Full Scorecard <-> Hole by Hole -- same active/inactive pill-toggle
    pattern used elsewhere in the app (e.g. home.py's handicap panel,
    tournament.py's leaderboard view toggle), just with its own CSS class
    names (t3g-scorecard-view-toggle*) rather than reusing one of those,
    matching how each of those features already has its own self-
    contained toggle rather than sharing one across unrelated pages."""
    holebyhole_class = "t3g-scorecard-view-toggle-button" + (
        " t3g-scorecard-view-toggle-button--active" if active_view == "holebyhole" else ""
    )
    full_class = "t3g-scorecard-view-toggle-button" + (
        " t3g-scorecard-view-toggle-button--active" if active_view == "full" else ""
    )
    return html.Div(
        className="t3g-scorecard-view-toggle",
        children=[
            html.Button(
                "Hole by Hole",
                id="live-round-view-holebyhole-button",
                className=holebyhole_class,
                n_clicks=0,
            ),
            html.Button(
                "Full Scorecard",
                id="live-round-view-full-button",
                className=full_class,
                n_clicks=0,
            ),
        ],
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


def render_live_round_body(round_data, player_id, initial_view="holebyhole"):
    """Everything below the (optional) tournament back-navigation --
    stores, the scorecard panel itself, the score/scrap/leave modals -- as
    a flat list of children, not wrapped in a page-level div. Called by
    both live_round.py's own layout() (the standalone /live-round page)
    and tournament.py's _live_round_panel (embedded straight into the
    tournament page's Live Round tab) -- "just show the scorecard of the
    round that is underway" only works if the markup itself is reusable
    like this, not just the page it lives on. Every id in here is a fixed
    singleton (not round-scoped), which is safe because a player can only
    ever have one round -- casual or tournament -- actually rendering this
    at a time; see the tournament_scope split in backend/services/
    rounds.py for why that's also true for tournament rounds specifically,
    not just casual ones.

    initial_view ("holebyhole" or "full") lets a caller open straight into
    the Full Scorecard instead of the usual default -- live_round.py's
    layout() passes "full" through when it's reached via the ?view=full
    query param a rejected sign-off's redirect carries, so the round
    reopens somewhere a player can see every hole at once to find what
    needs fixing, rather than having to click through hole by hole.
    Tournament.py's embedded panel never passes this, so it keeps the
    plain default."""
    is_manual = round_data.get("is_manual")
    is_owner_of_round = bool(round_data.get("is_owner"))
    is_tournament_round = bool(round_data.get("tournament_round_id"))
    players = round_data.get("players", [])
    pending_invites = round_data.get("pending_invites", [])
    owner_player = next((p for p in players if p.get("is_owner")), None)

    for p in players:
        p["is_viewer"] = p["player_id"] == player_id

    is_viewer_member = any(p.get("is_viewer") for p in players)

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
            "is_viewer": p.get("is_viewer", False),
            "holes": {str(h["hole_number"]): h for h in p["holes"]},
        }
        for p in players
    ]

    initial_hole = _first_unscored_hole(players_store_data)

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
    if round_data.get("status") == "in_progress":
        if is_tournament_round:
            # Unchanged from before -- every grouping member is an equal
            # is_owner=True (see start_tournament_round), so Finish and
            # Scrap both stay available to anyone in the grouping. No
            # Leave button here: a tournament round is an official
            # competition round for the whole tee-time group, not a
            # casual game any one player can just step out of -- see
            # leave_round's docstring in backend/services/rounds.py.
            # NR Round is new -- a self-service "I can't continue" for
            # tournament rounds specifically, filling this player's own
            # scorecard with No Return instead of removing them from the
            # round the way Leave does for a casual one (see mark_round_
            # no_result's docstring). Every accepted player can use it on
            # their own card, same reach as Finish/Scrap here.
            if is_owner_of_round:
                header_actions = [
                    html.Button(
                        "Scrap Round",
                        id="live-round-scrap-button",
                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                    ),
                    html.Button(
                        "NR Round",
                        id="live-round-nr-button",
                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                    ),
                    html.Button(
                        "Finish Round",
                        id="live-round-finish-button",
                        className="t3g-panel-action-button",
                    ),
                ]
        else:
            # Casual round: any accepted player can Finish -- is_owner_of_
            # round used to gate this too, which for a casual round meant
            # only its creator ever saw the button at all. Scrap stays
            # creator-only (is_owner_of_round, which for a casual round
            # specifically means round.player_id == viewer -- see
            # _apply_viewer_is_owner) since it deletes the round for
            # everyone in it, not just themselves. A non-creator accepted
            # player who wants out gets Leave Round instead, which only
            # removes their own participation.
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
            elif is_viewer_member:
                header_actions = [
                    html.Button(
                        "Leave Round",
                        id="live-round-leave-button",
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

    # Hole by Hole is the default view -- see components/live_scorecard.py's
    # module docstring update / the mobile scoring work this was built
    # for: entering one hole's scores for the whole group at a time reads
    # much better on a phone than the full 18-column table, which is kept
    # around (behind the toggle, still the default in spirit for anyone
    # who wants to see everything at once) rather than replaced outright.
    # initial_view overrides that default when a caller has a good reason
    # to (see this function's docstring) -- falls back to "holebyhole"
    # whenever there's no scorecard to show a view of at all.
    effective_initial_view = initial_view if players else "holebyhole"
    holeview_style = {} if (players and effective_initial_view == "holebyhole") else {"display": "none"}
    full_style = {} if (players and effective_initial_view == "full") else {"display": "none"}

    return [
        dcc.Store(id="live-round-id-store", data=round_data["id"]),
        dcc.Store(id="live-round-owner-id-store", data=owner_player["player_id"] if owner_player else None),
        dcc.Store(id="live-round-players-store", data=players_store_data),
        dcc.Store(id="live-round-active-hole-store"),
        dcc.Store(id="live-round-view-mode-store", data=effective_initial_view),
        dcc.Store(id="live-round-holeview-hole-store", data=initial_hole),
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
                        _scorecard_view_toggle(effective_initial_view) if players else None,
                        html.Div(
                            id="live-round-holeview-container",
                            style=holeview_style,
                            children=_hole_by_hole_panel_content(initial_hole, players_store_data),
                        ),
                        html.Div(
                            id="live-round-full-view-container",
                            style=full_style,
                            children=_scorecard_table(players, owner_player, is_manual, is_owner_of_round)
                            if players
                            else None,
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
                        # "NR" is a third save action, tournament rounds
                        # only -- it saves this one hole as No Return
                        # regardless of whatever's currently sitting in the
                        # shots/putts steppers, instead of the numeric
                        # payload "Enter" sends. See save_score in
                        # live_round.py, which branches on which of these
                        # two buttons actually triggered it -- everything
                        # downstream of that (closing the modal, Hole by
                        # Hole auto-advance, the hole-18 switch to Full
                        # Scorecard) is identical either way, since marking
                        # a hole NR is "done with this hole" exactly the
                        # same as entering a real score is.
                        #
                        # BUG FIX: this button used to be left OUT of the
                        # tree entirely for a casual round (the `if
                        # is_tournament_round else []` this replaced), not
                        # just hidden -- but play.py's save_score callback
                        # always declares
                        # Input("live-round-score-nr-save", "n_clicks")
                        # unconditionally, since one callback has to handle
                        # both buttons for whichever hole/round is
                        # currently open. Dash requires every id named in a
                        # callback's Input/State/Output list to exist
                        # somewhere in the CURRENT layout -- for a casual
                        # round, this one never did, so the very first
                        # score save on a casual round threw "a nonexistent
                        # object was used in an Input of a Dash callback"
                        # and nothing saved. Rendering the button always,
                        # and hiding it with a style instead of omitting
                        # it, keeps the id permanently present for the
                        # callback graph while still keeping it invisible
                        # (and unclickable, via pointer-events) outside a
                        # tournament round.
                        dbc.Button(
                            "NR",
                            id="live-round-score-nr-save",
                            color="warning",
                            outline=True,
                            style={} if is_tournament_round else {"display": "none"},
                        ),
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
        dbc.Modal(
            id="live-round-leave-modal",
            is_open=False,
            children=[
                dbc.ModalHeader(dbc.ModalTitle("Leave this round?")),
                dbc.ModalBody(
                    "You'll be removed from this round and lose access to it, but it'll keep going "
                    "for everyone else still in it. This can't be undone."
                ),
                dbc.ModalFooter(
                    [
                        dbc.Button("Cancel", id="live-round-leave-cancel", color="secondary"),
                        dbc.Button(
                            "Leave Round",
                            id="live-round-leave-confirm",
                            color="danger",
                        ),
                    ]
                ),
            ],
        ),
        dbc.Modal(
            id="live-round-nr-modal",
            is_open=False,
            children=[
                dbc.ModalHeader(dbc.ModalTitle("Mark this round No Result?")),
                dbc.ModalBody(
                    "This fills every hole you haven't scored yet on your own scorecard with No "
                    "Return -- any hole you've already entered a real score for is left exactly as "
                    "it is. Your round will show as NR on the leaderboard, sorted below every other "
                    "player. It doesn't affect anyone else still playing, and you can still turn an "
                    "individual hole back into a real score afterward by entering it again."
                ),
                dbc.ModalFooter(
                    [
                        dbc.Button("Cancel", id="live-round-nr-cancel", color="secondary"),
                        dbc.Button(
                            "NR Round",
                            id="live-round-nr-confirm",
                            color="danger",
                        ),
                    ]
                ),
            ],
        ),
        dcc.Location(id="live-round-finish-redirect", refresh=True),
    ]