# target path: frontend/src/pages/live_round.py (full replacement)
import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from components.live_scorecard import (
    _hole_by_hole_panel_content,
    _hole_par,
    _result_badge,
    _score_marking_class,
    _score_total_text,
    render_live_round_body,
)
from config import API_BASE_URL

dash.register_page(__name__, path="/live-round", name="Live Round")

_FAIRWAY_RADIO_TO_BOOL = {"yes": True, "no": False}
_FAIRWAY_BOOL_TO_RADIO = {True: "yes", False: "no"}

# Same literal class strings as tournament.py's _TAB_BUTTON_BASE/_ACTIVE --
# duplicated rather than imported across pages (these two modules don't
# otherwise depend on each other, and Dash Pages modules aren't really
# meant to import from one another -- see components/live_scorecard.py's
# module docstring for why that specifically breaks page registration) so
# this page's back-to-tournament subnav visually matches the real one
# exactly.
_TAB_BUTTON_BASE = "t3g-tournament-tab"
_TAB_BUTTON_ACTIVE = "t3g-tournament-tab t3g-tournament-tab--active"


def _tournament_context_subnav(tournament_id, club_slug):
    """Reached when the round being scored is a tournament round (see
    round_data's tournament_id/tee_time_id) -- keeps the tournament's own
    subnav visible above the scorecard instead of leaving the page a dead
    end back to "/" only. Tournament Info/Start Sheet/Leaderboard are real
    navigation links back to the tournament page (Start Sheet/Leaderboard
    carry ?tab= so they land on the right tab there, same query param
    tournament.py's own layout() reads -- see _tab_visibility), Live Round
    is shown as the (non-clickable) active tab since that's where we
    already are."""
    base_href = f"/clubs/{club_slug}/tournaments/{tournament_id}"
    return html.Div(
        className="t3g-tournament-subnav",
        children=html.Div(
            className="t3g-tournament-subnav-inner",
            children=[
                html.Div(
                    className="t3g-tournament-tabs",
                    children=[
                        dcc.Link("Tournament Info", href=base_href, className=_TAB_BUTTON_BASE),
                        dcc.Link(
                            "Start Sheet", href=f"{base_href}?tab=startsheet", className=_TAB_BUTTON_BASE
                        ),
                        dcc.Link(
                            "Leaderboard", href=f"{base_href}?tab=leaderboard", className=_TAB_BUTTON_BASE
                        ),
                        html.Span("Live Round", className=_TAB_BUTTON_ACTIVE),
                    ],
                ),
                dcc.Link(
                    "Return to Club",
                    href=f"/clubs/{club_slug}",
                    className="t3g-tournament-subnav-back",
                ),
            ],
        ),
    )


def layout(round_id=None, **kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="live-round-redirect-signin", refresh=True)

    if round_id:
        # Reached from the tournament page's Live Round tab (Start/
        # Continue Live Round both link here with ?round_id=...) rather
        # than the default no-argument load. Fetched directly by id
        # instead of through the "my one active round" lookup below,
        # because a player can now have a casual round *and* a tournament
        # round live at the same time (see _get_active_round_id_for_
        # player's tournament_scope split in backend/services/rounds.py)
        # -- /rounds/active/{player_id} can only ever return one of them.
        response = requests.get(
            f"{API_BASE_URL}/rounds/{round_id}", params={"viewer_player_id": player_id}
        )
    else:
        # No round_id in the URL -- there can only ever be one *casual*
        # round (owned or joined) a player is an accepted participant in
        # at a time, so looking it up by player_id is exactly what lets
        # this page "pick up where you left off" after closing and
        # reopening the app, with no state to restore. Doesn't surface a
        # tournament round even if one's live -- those are only reached
        # via the tournament page's own Continue Live Round link, above.
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
    players = round_data.get("players", [])
    pending_invites = round_data.get("pending_invites", [])

    # A round reached by explicit id (the tournament path above) isn't
    # otherwise scoped to "rounds I belong to" the way the default lookup
    # is -- guard against a stale/guessed round_id showing someone else's
    # scorecard to a player who was never part of it.
    if round_id and not any(p["player_id"] == player_id for p in players + pending_invites):
        return html.Div(
            className="t3g-page",
            children=html.Div(
                className="t3g-panel",
                children=html.Div(
                    className="t3g-panel-body",
                    children=[
                        html.P("You're not part of this round.", className="t3g-empty-state"),
                        dcc.Link("Back to home", href="/", className="t3g-link-button"),
                    ],
                ),
            ),
        )

    tournament_subnav = None
    if round_data.get("tournament_id") and round_data.get("club_slug"):
        tournament_subnav = _tournament_context_subnav(round_data["tournament_id"], round_data["club_slug"])

    body_children = render_live_round_body(round_data, player_id)
    return html.Div(
        className="t3g-page",
        children=([tournament_subnav] if tournament_subnav else []) + body_children,
    )


def _patch_hole(round_id, player_id, hole_number, field, value):
    # updated_by is always the viewer making this request (whoever's
    # session it is), not necessarily player_id (the person whose hole is
    # being edited) -- the backend checks updated_by against round
    # membership, matching casual rounds' "anyone in the group can score
    # anyone in the group" model.
    response = requests.patch(
        f"{API_BASE_URL}/rounds/{round_id}/players/{player_id}/holes/{hole_number}",
        json={field: value},
        params={"updated_by": session.get("player_id")},
    )
    if response.status_code == 200:
        return ""
    try:
        return response.json().get("detail", "Couldn't save that value.")
    except ValueError:
        return "Couldn't save that value."


def _find_player(players, player_id):
    return next((p for p in (players or []) if p["player_id"] == player_id), None)


def _modal_open_state(player, hole_number, source):
    """Everything the Enter Score modal needs to open pre-filled for one
    player's one hole -- shared by toggle_score_modal (a fresh tap on a
    score button) and save_score's Hole by Hole auto-advance (jumping
    straight to the next player without the user tapping anything), so
    both populate the modal identically. Returns the exact 12-tuple
    toggle_score_modal's Outputs expect, in that same order: is_open,
    title, par, shots value, shots display, putts value, putts display,
    fairway radio value, fairway row style, badge text, badge className,
    active-hole-store data.

    `source` ("full" or "holebyhole") records which view this open came
    from, carried on live-round-active-hole-store's own data -- that's
    what lets save_score later tell whether THIS save should auto-advance
    at all: only a Hole by Hole open should ever chain to the next
    player/hole; a Full Scorecard tap should always just close back to
    the Full Scorecard, however the score got there.
    """
    holes = player.get("holes") or {}
    hole = holes.get(str(hole_number), {})
    par = _hole_par(hole)
    strokes = hole.get("strokes")
    putts = hole.get("putts")

    # Nothing entered for this hole yet -- start the steppers at a
    # sensible guess (par, 2 putts) instead of blank, so most holes are
    # just a tap or two away instead of building the number from zero. A
    # hole that's already been scored always shows its real values.
    if strokes is None and par is not None:
        strokes = par
    if putts is None:
        putts = 2

    badge_text, badge_class = _result_badge(strokes, par)

    # Fairway hit isn't a meaningful stat on a par 3 -- there's normally
    # no lay-up shot to a fairway, you're going straight at the green --
    # so hide the toggle rather than ask a question that doesn't apply.
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
        {"player_id": player.get("player_id"), "hole_number": hole_number, "source": source},
    )


def _next_unscored_player(players, hole_number, after_player_id):
    """Cycles through `players` (already updated in-memory with whatever
    was just saved) starting right after after_player_id and wrapping
    around the whole group, returning the first one still missing a
    strokes value for hole_number -- or None once everyone has one. This
    -- not just literal list-order "the next index" -- is what "take you
    to the next player" means in practice: skip straight past anyone
    already scored, including looping back around to someone earlier in
    row order who hasn't been reached yet."""
    ids_in_order = [p["player_id"] for p in players]
    start = ids_in_order.index(after_player_id) + 1 if after_player_id in ids_in_order else 0
    ordered = players[start:] + players[:start]
    for p in ordered:
        hole = (p.get("holes") or {}).get(str(hole_number), {})
        if hole.get("strokes") is None:
            return p
    return None


def _view_switch_state(view):
    """(view, holeview_style, full_style, holebyhole_class, full_class)
    for switching the scorecard between Full Scorecard and Hole by Hole --
    shared by switch_scorecard_view (the toggle buttons, below) and
    save_score's own auto-switch to Full Scorecard once hole 18 is fully
    scored, so both ways of landing on a view build the exact same
    style/className state instead of two copies of this logic drifting
    apart from each other."""
    holeview_style = {} if view == "holebyhole" else {"display": "none"}
    full_style = {"display": "none"} if view == "holebyhole" else {}
    holebyhole_class = "t3g-scorecard-view-toggle-button" + (
        " t3g-scorecard-view-toggle-button--active" if view == "holebyhole" else ""
    )
    full_class = "t3g-scorecard-view-toggle-button" + (
        " t3g-scorecard-view-toggle-button--active" if view == "full" else ""
    )
    return view, holeview_style, full_style, holebyhole_class, full_class


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
    Input({"type": "live-round-holeview-score-button", "player": ALL, "hole": ALL}, "n_clicks"),
    Input("live-round-score-cancel", "n_clicks"),
    State("live-round-players-store", "data"),
    prevent_initial_call=True,
)
def toggle_score_modal(button_clicks, holeview_button_clicks, cancel_clicks, players):
    # Full Scorecard and Hole by Hole each have their own score buttons
    # (distinct id "type"s -- see _holeview_score_button's docstring in
    # components/live_scorecard.py for why they can't share one), but both
    # open this exact same modal the exact same way via the shared
    # _modal_open_state helper -- the only thing that differs is which
    # "source" gets tagged onto live-round-active-hole-store's data.
    # save_score (below) reads that back afterwards to decide whether
    # saving should just close the modal (Full Scorecard) or chain
    # straight on to the next player/hole (Hole by Hole).
    triggered_id = dash.ctx.triggered_id
    no_update = dash.no_update

    if triggered_id == "live-round-score-cancel":
        return (
            False, no_update, no_update, no_update, no_update,
            no_update, no_update, no_update, no_update, no_update, no_update, None,
        )

    if isinstance(triggered_id, dict) and triggered_id.get("type") in (
        "live-round-score-button",
        "live-round-holeview-score-button",
    ):
        all_clicks = (button_clicks or []) + (holeview_button_clicks or [])
        if not any(all_clicks):
            # The set of buttons re-rendering also fires this (all
            # n_clicks reset to 0) -- only actually open on a real click.
            raise PreventUpdate

        hole_number = triggered_id["hole"]
        clicked_player_id = triggered_id["player"]
        player = _find_player(players, clicked_player_id) or {"player_id": clicked_player_id}
        source = "holebyhole" if triggered_id["type"] == "live-round-holeview-score-button" else "full"

        return _modal_open_state(player, hole_number, source)

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


# The full ordered set of save_score's outputs, and matching names used
# by _save_score_result below to build each branch's return tuple by
# name instead of by position. save_score's branches each touch a wildly
# different subset of these 20 outputs (an error only touches 3 of them;
# chaining to the next Hole by Hole player touches a different dozen than
# advancing to the next hole does) -- building the tuple from a
# dash.no_update-everywhere dict and overriding just what changed avoids
# hand-counting no_update placeholders per branch, which is exactly the
# kind of thing that quietly goes stale the next time an output gets
# added or reordered.
_SAVE_SCORE_OUTPUTS = (
    Output("live-round-score-modal", "is_open", allow_duplicate=True),
    Output("live-round-players-store", "data", allow_duplicate=True),
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-active-hole-store", "data", allow_duplicate=True),
    Output("live-round-holeview-hole-store", "data", allow_duplicate=True),
    Output("live-round-view-mode-store", "data", allow_duplicate=True),
    Output("live-round-holeview-container", "style", allow_duplicate=True),
    Output("live-round-full-view-container", "style", allow_duplicate=True),
    Output("live-round-view-holebyhole-button", "className", allow_duplicate=True),
    Output("live-round-view-full-button", "className", allow_duplicate=True),
    Output("live-round-score-modal-title", "children", allow_duplicate=True),
    Output("live-round-score-modal-par-store", "data", allow_duplicate=True),
    Output("live-round-score-shots-store", "data", allow_duplicate=True),
    Output("live-round-score-shots-display", "children", allow_duplicate=True),
    Output("live-round-score-putts-store", "data", allow_duplicate=True),
    Output("live-round-score-putts-display", "children", allow_duplicate=True),
    Output("live-round-score-fairway-input", "value", allow_duplicate=True),
    Output("live-round-score-fairway-row", "style", allow_duplicate=True),
    Output("live-round-score-result-badge", "children", allow_duplicate=True),
    Output("live-round-score-result-badge", "className", allow_duplicate=True),
)
_SAVE_SCORE_OUTPUT_NAMES = (
    "modal_is_open", "players_store", "error", "active_hole_store",
    "holeview_hole_store", "view_mode_store", "holeview_container_style",
    "full_view_container_style", "holebyhole_button_class", "full_button_class",
    "modal_title", "modal_par_store", "shots_store", "shots_display",
    "putts_store", "putts_display", "fairway_value", "fairway_row_style",
    "badge_children", "badge_className",
)


def _save_score_result(**overrides):
    values = {name: dash.no_update for name in _SAVE_SCORE_OUTPUT_NAMES}
    values.update(overrides)
    return tuple(values[name] for name in _SAVE_SCORE_OUTPUT_NAMES)


@callback(
    *_SAVE_SCORE_OUTPUTS,
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
    # Rounds finished before this "source" tracking existed would have
    # no key here, but active_hole is always freshly set by
    # toggle_score_modal right before this can ever fire in practice, so
    # this default is really just a defensive fallback, not an expected
    # path -- falling back to "full" (no auto-advance) is the safe
    # choice if it's ever missing.
    source = active_hole.get("source", "full")

    # Par 3s don't get a fairway hit toggle in the UI -- also ignore
    # whatever's in the radio's stale value here, rather than trusting a
    # hidden control not to leak a selection into the save.
    fairway_hit = None if par == 3 else _FAIRWAY_RADIO_TO_BOOL.get(fairway_radio)

    response = requests.patch(
        f"{API_BASE_URL}/rounds/{round_id}/players/{target_player_id}/holes/{hole_number}",
        json={"strokes": shots, "putts": putts, "fairway_hit": fairway_hit},
        params={"updated_by": session.get("player_id")},
    )

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", "Couldn't save that score.")
        except ValueError:
            detail = "Couldn't save that score."
        return _save_score_result(error=detail)

    players = [dict(p) for p in (players or [])]
    for p in players:
        if p["player_id"] == target_player_id:
            holes = dict(p["holes"])
            hole = dict(holes.get(str(hole_number), {}))
            hole.update({"strokes": shots, "putts": putts, "fairway_hit": fairway_hit})
            holes[str(hole_number)] = hole
            p["holes"] = holes

    # A Full Scorecard save has nothing to chain to -- the view never
    # changed to open this modal in the first place, so there's nothing
    # to navigate; just close it and land right back on the Full
    # Scorecard, exactly where the tap that opened it came from. This is
    # also what makes "no loading icon" true here in practice: none of
    # this callback's outputs touch "_pages_content", so app.py's
    # page-level spinner (now scoped via target_components -- see that
    # file) never fires for a save regardless of which view it came from.
    if source != "holebyhole":
        return _save_score_result(
            modal_is_open=False, players_store=players, error="", active_hole_store=None,
        )

    # Hole by Hole: chain straight to whichever player still needs a
    # score for this same hole, wrapping the group starting right after
    # whoever was just scored -- lets one person rattle straight through
    # everyone's score for a hole without re-tapping each score button by
    # hand.
    next_player = _next_unscored_player(players, hole_number, target_player_id)
    if next_player is not None:
        (
            modal_is_open, title, modal_par, shots_val, shots_display,
            putts_val, putts_display, fairway_value, fairway_row_style,
            badge_children, badge_className, active_hole_data,
        ) = _modal_open_state(next_player, hole_number, "holebyhole")
        return _save_score_result(
            modal_is_open=modal_is_open,
            players_store=players,
            error="",
            active_hole_store=active_hole_data,
            modal_title=title,
            modal_par_store=modal_par,
            shots_store=shots_val,
            shots_display=shots_display,
            putts_store=putts_val,
            putts_display=putts_display,
            fairway_value=fairway_value,
            fairway_row_style=fairway_row_style,
            badge_children=badge_children,
            badge_className=badge_className,
        )

    # Everyone's scored for this hole -- move on. Hole 18 finishing is
    # treated as "the round's essentially done" -- land on the Full
    # Scorecard to review the whole card instead of trying to advance to
    # a hole 19 that doesn't exist. Any earlier hole just advances Hole
    # by Hole to the next one with the modal closed, ready for the group
    # to tap into that hole's first player whenever they get there --
    # deliberately not auto-opening the next hole's modal too, since
    # walking to the next tee takes a beat in real life and there's no
    # "next player" to chain to yet until someone actually taps in.
    if hole_number >= 18:
        view, holeview_style, full_style, holebyhole_class, full_class = _view_switch_state("full")
        return _save_score_result(
            modal_is_open=False, players_store=players, error="", active_hole_store=None,
            view_mode_store=view,
            holeview_container_style=holeview_style,
            full_view_container_style=full_style,
            holebyhole_button_class=holebyhole_class,
            full_button_class=full_class,
        )

    return _save_score_result(
        modal_is_open=False, players_store=players, error="", active_hole_store=None,
        holeview_hole_store=hole_number + 1,
    )


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


@callback(
    Output("live-round-view-mode-store", "data"),
    Output("live-round-holeview-container", "style"),
    Output("live-round-full-view-container", "style"),
    Output("live-round-view-holebyhole-button", "className"),
    Output("live-round-view-full-button", "className"),
    Input("live-round-view-holebyhole-button", "n_clicks"),
    Input("live-round-view-full-button", "n_clicks"),
    prevent_initial_call=True,
)
def switch_scorecard_view(holebyhole_clicks, full_clicks):
    view = "full" if dash.ctx.triggered_id == "live-round-view-full-button" else "holebyhole"
    return _view_switch_state(view)


@callback(
    Output("live-round-holeview-hole-store", "data"),
    Input("live-round-holeview-prev", "n_clicks"),
    Input("live-round-holeview-next", "n_clicks"),
    State("live-round-holeview-hole-store", "data"),
    prevent_initial_call=True,
)
def navigate_holeview_hole(prev_clicks, next_clicks, current_hole):
    # The prev/next buttons live inside live-round-holeview-container's
    # own children, which render_holeview_panel below fully replaces on
    # every hole change or players-store update -- that recreates these
    # buttons from scratch each time (n_clicks reset to 0), which is the
    # same kind of "the set of buttons re-rendering also fires this"
    # phantom trigger toggle_score_modal already has to guard against
    # above. Checking the actual triggered *value* (not just which id
    # triggered) is what tells a real tap apart from that.
    triggered = dash.ctx.triggered[0] if dash.ctx.triggered else None
    if not triggered or not triggered.get("value"):
        raise PreventUpdate

    current_hole = current_hole or 1
    if dash.ctx.triggered_id == "live-round-holeview-prev":
        return max(1, current_hole - 1)
    if dash.ctx.triggered_id == "live-round-holeview-next":
        return min(18, current_hole + 1)
    raise PreventUpdate


@callback(
    Output("live-round-holeview-container", "children"),
    Input("live-round-holeview-hole-store", "data"),
    Input("live-round-players-store", "data"),
    prevent_initial_call=True,
)
def render_holeview_panel(hole_number, players):
    # The initial paint of this container's content comes straight from
    # render_live_round_body/layout() (the exact same _hole_by_hole_panel_
    # content call, just made once up front) -- this callback only needs
    # to run for what happens *after* that: navigating to a different hole,
    # or a score getting saved (from either view) updating players-store.
    # save_score (above) can update both of this callback's Inputs in the
    # same round trip when it auto-advances Hole by Hole to the next hole
    # -- Dash batches simultaneous input changes into one call here, so
    # this still only fires once and renders the new hole with the
    # already-updated scores, not twice with a stale hole/players mix.
    if not hole_number or not players:
        raise PreventUpdate
    return _hole_by_hole_panel_content(hole_number, players)