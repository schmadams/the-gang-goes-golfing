# target path: frontend/src/pages/tournament.py (full replacement)
import time

import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, MATCH, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from components.live_scorecard import render_live_round_body
from components.scorecard import live_badge
from components.spinner import golf_swing_spinner
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

_ENTERED_STATUSES = ("pending", "confirmed")

# Same options/labels as club.py's create-tournament modal, and same
# reasoning as _TOURNAMENT_FORMAT_LABELS above for keeping a separate copy
# here rather than sharing one -- the edit modal below is a duplicate of
# that one (own "tournament-edit-" id prefix throughout) so an admin
# editing a tournament sees literally the same form they created it with.
_TOURNAMENT_FORMAT_OPTIONS = [
    {"label": "Scratch", "value": "scratch"},
    {"label": "Stableford", "value": "stableford"},
    {"label": "Net", "value": "net"},
    {"label": "2BBB (Better Ball)", "value": "2bbb"},
    {"label": "4BBB (Better Ball)", "value": "4bbb"},
    {"label": "Texas Scramble", "value": "texas_scramble"},
]
_TOURNAMENT_ENTRY_MODE_OPTIONS = [
    {"label": "Anyone can join directly", "value": "self"},
    {"label": "Applications need approval", "value": "approval"},
]
_TOURNAMENT_GROUPING_METHOD_OPTIONS = [
    {"label": "Random", "value": "random"},
    {"label": "By handicap", "value": "handicap"},
    {"label": "Manual", "value": "manual"},
]
_GROUP_SIZE_OPTIONS = [{"label": f"{n} per group", "value": n} for n in range(2, 7)]
_DEFAULT_GROUP_SIZE = 4

# Same reasoning/values as club.py's copy -- see that module for why these
# are duplicated as plain constants instead of imported from the backend.
_MAX_HANDICAP_INDEX = 54
_MIN_HANDICAP_FLOOR = -10


def _adjust_handicap_stepper(triggered_id, plus_id, minus_id, current):
    """Same +/- logic as club.py's copy -- see that function's docstring."""
    value = current
    if triggered_id == plus_id:
        value = 0 if value is None else min(value + 1, _MAX_HANDICAP_INDEX)
    elif triggered_id == minus_id:
        value = -1 if value is None else (value - 1 if value > _MIN_HANDICAP_FLOOR else None)
    return value, str(value) if value is not None else "–"


def _course_label(course):
    # Same field names/format as club.py's copy (and home.py's/
    # my_account.py's) -- kept as its own copy per page rather than a
    # shared import, matching how this app already duplicates it.
    label = course["club_name"]
    if course.get("course_name"):
        label += f" — {course['course_name']}"
    location = course.get("county") or course.get("postcode")
    return f"{label} ({location})" if location else label


def _entrant_label(entrant):
    return (
        entrant.get("nickname")
        or f"{entrant.get('first_name', '')} {entrant.get('surname', '')}".strip()
        or "Unknown player"
    )


def _roster_player_label(player):
    player = player or {}
    return (
        player.get("nickname")
        or f"{player.get('first_name', '')} {player.get('surname', '')}".strip()
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


def _format_tee_time(tee_time_str):
    """"HH:MM:SS" (what Postgres' time column serializes to over the API)
    -> "H:MM AM/PM". Built by hand rather than via strftime's %-I/%#I,
    since which of those strips the leading zero is platform-dependent
    (Unix vs Windows) and this needs to render the same regardless of
    where the Dash server happens to be running."""
    if not tee_time_str:
        return ""
    hour, minute = int(tee_time_str[:2]), int(tee_time_str[3:5])
    period = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {period}"


def _recap_row(cells, header=False):
    """One row of the read-only tee time recap -- a fixed Tee Time column
    plus one column per player slot (Player A/B/C/...), truncated with an
    ellipsis (title attr carries the full name for a hover tooltip) rather
    than wrapping/overflowing, with a literal "|" divider between each pair
    of player columns -- not between Tee Time and Player A, just between the
    players themselves. Reused for both the header labels row and every
    data row so the two stay pixel-aligned (same flex proportions, same
    number of divider cells)."""
    row_children = []
    for index, cell in enumerate(cells):
        if index > 1:
            row_children.append(html.Span("|", className="t3g-teetime-recap-divider"))
        is_time_col = index == 0
        row_children.append(
            html.Span(
                cell,
                className="t3g-teetime-recap-cell"
                + (" t3g-teetime-recap-cell--time" if is_time_col else ""),
                title=(None if header or is_time_col else cell),
            )
        )
    class_name = "t3g-teetime-recap-row" + (" t3g-teetime-recap-row--header" if header else "")
    return html.Div(row_children, className=class_name)


def _first_unfinished_prior_round(tournament, target_round_number, player_id):
    """Frontend mirror of backend/services/rounds.py's
    _first_unfinished_prior_round_number -- tournament rounds have to be
    played in order, so before showing a clickable Start Live Round
    button for a given round, walk every earlier round_number in this
    tournament (ascending, using the round list already on hand from the
    page load -- no extra request) and return the first one the viewer
    was grouped into but hasn't completed, or None if there isn't one.
    This copy only decides whether to show a button or an explanatory
    message; the POST to /rounds/tournament/{tee_time_id} is still the
    real, authoritative gate -- see start_tournament_round."""
    for r in sorted(tournament.get("rounds", []), key=lambda r: r["round_number"]):
        if r["round_number"] >= target_round_number:
            continue
        group = next(
            (g for g in r.get("tee_times", []) if any(p["player_id"] == player_id for p in g.get("players", []))),
            None,
        )
        if group is None:
            continue
        live_round = group.get("live_round")
        if not live_round or live_round.get("status") != "completed":
            return r["round_number"]
    return None


def _my_group_action(tournament, r, my_group, player_id):
    """The viewer's own tee time + live round action for one round,
    rendered on the Start Sheet right alongside the tee times themselves
    rather than off on a separate Live Round tab -- Start Live Round is a
    per-grouping action on the round you're actually looking at, so it
    belongs where the groupings are. Mirrors the states _live_round_panel
    used to render on its own (not-started/in-progress/completed), but
    only this copy ever shows the actual clickable button -- the Live
    Round tab now just reflects whatever state this produced."""
    round_id = r["id"]
    tee_time_id = my_group["id"]
    live_round = my_group.get("live_round")

    groupmate_labels = [
        _entrant_label(p) for p in my_group.get("players", []) if p["player_id"] != player_id
    ]
    meta_text = f"Your tee time: {_format_tee_time(my_group.get('tee_time'))}"
    if groupmate_labels:
        meta_text += f" with {', '.join(groupmate_labels)}"

    if live_round is None:
        blocking_round_number = _first_unfinished_prior_round(tournament, r["round_number"], player_id)
        if blocking_round_number is not None:
            action = html.Span(
                f"Finish Round {blocking_round_number} before you can start this round.",
                className="t3g-empty-state",
            )
        else:
            action = html.Button(
                "Start Live Round",
                id={
                    "type": "tournament-start-live-round",
                    "round_id": round_id,
                    "tee_time_id": tee_time_id,
                },
                className="t3g-panel-action-button",
                n_clicks=0,
            )
    elif live_round.get("status") == "in_progress":
        action = html.Span("Live round in progress", className="t3g-liveround-inprogress-badge")
    else:
        action = html.Span("Round completed", className="t3g-liveround-finished-badge")

    return html.Div(
        className="t3g-liveround-group",
        children=[html.Div(meta_text, className="t3g-liveround-group-meta"), action],
    )


def _tee_times_panel(tournament, is_admin, player_id):
    """Rendered inside its own "Start Sheet" subnav tab (see
    _tournament_subnav / switch_tournament_tab) rather than as a section of
    Tournament Info -- one block per round, each showing whatever groups
    have been generated plus, for admins, a first-tee-time input and a
    Generate button. Generating is always a full wholesale regenerate (see
    generate_tee_times's docstring) -- no confirmation, since re-running it
    after a withdrawal/late add is the expected, ordinary workflow, not a
    destructive edge case. When the tournament's grouping_method is
    "manual", Generate still creates the slots (spaced/sized the same way)
    but leaves them empty -- admins place confirmed entrants into them one
    by one via the dropdowns _management_table builds below, instead of the
    auto methods sorting the whole field for you.

    Layout is split in two, top to bottom: an admin-only management table
    (editable tee time per slot, plus -- for manual mode -- the player
    assignment dropdowns) and, below that, a plain read-only recap of
    whatever's actually saved. All editing lives in the management table;
    the recap never has inputs in it, admin or not, so it stays a clean
    "here's the current start sheet" view.

    Right under each round's heading, every viewer (admin or not) also
    gets their own tee time plus the Start Live Round action for it (see
    _my_group_action) -- starting a live round happens right here, next
    to the actual tee times, rather than on a separate Live Round tab."""
    rounds = tournament.get("rounds", [])
    is_manual = tournament.get("grouping_method") == "manual"
    confirmed_entrants = [e for e in tournament.get("entrants", []) if e.get("status") == "confirmed"]

    round_sections = []
    for r in rounds:
        round_id = r["id"]
        groups = r.get("tee_times", [])

        course_label = r.get("club_name") or "Course TBC"
        if r.get("course_name"):
            course_label += f", {r['course_name']}"
        round_heading = f"Round {r['round_number']} — {r.get('round_date', '')} ({course_label})"

        my_group = next(
            (g for g in groups if any(p["player_id"] == player_id for p in g.get("players", []))),
            None,
        )
        my_group_section = _my_group_action(tournament, r, my_group, player_id) if my_group else None

        if groups:
            recap_group_size = r.get("group_size") or _DEFAULT_GROUP_SIZE
            recap_header = _recap_row(
                ["Tee Time"] + [f"Player {chr(65 + i)}" for i in range(recap_group_size)], header=True
            )
            recap_data_rows = []
            for g in groups:
                slot_labels = [_entrant_label(p) for p in g.get("players", [])][:recap_group_size]
                slot_labels += ["—"] * (recap_group_size - len(slot_labels))
                recap_data_rows.append(_recap_row([_format_tee_time(g.get("tee_time"))] + slot_labels))
            group_rows = [recap_header] + recap_data_rows
        else:
            empty_text = (
                "No tee time slots created yet." if is_manual else "No tee times generated yet."
            )
            group_rows = [html.P(empty_text, className="t3g-empty-state mb-0")]

        admin_controls = None
        if is_admin:
            admin_controls = html.Div(
                className="t3g-teetime-generate-row",
                children=[
                    # dbc.Input, not dcc.Input -- dcc.Input's `type` prop is
                    # restricted by its JS PropTypes to a fixed list that
                    # doesn't include "time" (React throws a hard prop-type
                    # error for anything outside it). dbc.Input maps
                    # straight to a native <input> with no such
                    # restriction, so the browser's own time picker works.
                    dbc.Input(
                        id={"type": "tournament-teetime-first-time", "round_id": round_id},
                        type="time",
                        value="08:00",
                        className="t3g-teetime-time-input",
                    ),
                    html.Button(
                        "Create Tee Time Slots" if is_manual else "Generate Tee Times",
                        id={"type": "tournament-teetime-generate", "round_id": round_id},
                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                        n_clicks=0,
                    ),
                ],
            )

        management_table = None
        if is_admin and groups:
            # One row per tee time slot. Manual mode gets a fixed
            # "Player A/B/C/..." dropdown column per seat in the group
            # (round.group_size wide) same as before -- slot position within
            # a group is purely a frontend/display convenience, nothing
            # server-side tracks "Player A" vs "Player B", it's just however
            # each group's current player list happens to be ordered, so on
            # every reload the columns get re-filled left-to-right from that
            # order. Non-manual modes get a single read-only Players column
            # instead, since reassigning players isn't part of this table for
            # those methods -- only the tee time itself is editable there.
            group_size = r.get("group_size") or _DEFAULT_GROUP_SIZE
            entrant_options = [
                {"label": _entrant_label(e), "value": e["player_id"]} for e in confirmed_entrants
            ]
            # Fixed-minimum column widths (not minmax(0, ...)) -- on a
            # narrow phone, letting player columns squeeze all the way to
            # 0 is what caused labels/dropdown text to overlap and clip
            # (e.g. "Player B"'s label bleeding into "Player A"'s column).
            # minmax(88px, 1fr) means a column never goes below a legible
            # width; the row can end up wider than the viewport instead,
            # which is what .t3g-teetime-assign-table-scroll (wrapped
            # around this table below) is for -- same horizontal-scroll
            # pattern the read-only recap table already uses for the same
            # reason (see .t3g-teetime-group-list-scroll in club.css). The
            # Tee Time column itself is also narrower now (140px vs the
            # old 210px) since a compact time input + Save button doesn't
            # need that much room.
            grid_style = (
                {"gridTemplateColumns": f"140px repeat({group_size}, minmax(88px, 1fr))"}
                if is_manual
                else {"gridTemplateColumns": "140px 1fr"}
            )

            header_children = [html.Span("Tee Time", className="t3g-teetime-assign-col-label")]
            header_children += (
                [
                    html.Span(f"Player {chr(65 + i)}", className="t3g-teetime-assign-col-label")
                    for i in range(group_size)
                ]
                if is_manual
                else [html.Span("Players", className="t3g-teetime-assign-col-label")]
            )
            header_row = html.Div(
                className="t3g-teetime-assign-table-row t3g-teetime-assign-table-header",
                style=grid_style,
                children=header_children,
            )

            slot_rows = []
            for g in groups:
                # Editable time + Save button, same one-off-override
                # endpoint (update_tee_time_slot) regardless of grouping
                # method -- this doesn't touch who's in the group, only when
                # they tee off.
                time_cell = html.Div(
                    className="t3g-teetime-edit",
                    children=[
                        dbc.Input(
                            id={
                                "type": "tournament-teetime-update-input",
                                "round_id": round_id,
                                "tee_time_id": g["id"],
                            },
                            type="time",
                            value=(g.get("tee_time") or "")[:5] or None,
                            className="t3g-teetime-time-input t3g-teetime-time-input--inline",
                        ),
                        html.Button(
                            "Save",
                            id={
                                "type": "tournament-teetime-update-save",
                                "round_id": round_id,
                                "tee_time_id": g["id"],
                            },
                            className="t3g-teetime-save-button",
                            n_clicks=0,
                        ),
                    ],
                )

                row_children = [time_cell]
                if is_manual:
                    slot_player_ids = [p["player_id"] for p in g.get("players", [])][:group_size]
                    slot_player_ids += [None] * (group_size - len(slot_player_ids))
                    row_children.extend(
                        dcc.Dropdown(
                            id={
                                "type": "tournament-teetime-assign",
                                "round_id": round_id,
                                "tee_time_id": g["id"],
                                "slot": slot_index,
                            },
                            options=entrant_options,
                            value=player_id,
                            placeholder="Select player",
                            clearable=True,
                            className="t3g-teetime-assign-dropdown",
                        )
                        for slot_index, player_id in enumerate(slot_player_ids)
                    )
                else:
                    row_children.append(
                        html.Span(
                            ", ".join(_entrant_label(p) for p in g.get("players", [])) or "No players",
                            className="t3g-teetime-assign-players-readonly",
                        )
                    )
                slot_rows.append(
                    html.Div(
                        className="t3g-teetime-assign-table-row", style=grid_style, children=row_children
                    )
                )

            management_table = html.Div(
                className="t3g-teetime-manual-assign",
                children=[
                    # Holds the round's full confirmed-entrant option list so
                    # filter_tee_time_assign_options (below) can re-derive
                    # each dropdown's *available* options -- entrant_options
                    # minus whoever's already picked in one of the round's
                    # other dropdowns -- every time any dropdown's value
                    # changes, without a server round trip. Only needed in
                    # manual mode, since that's the only case with dropdowns
                    # to filter.
                    dcc.Store(
                        id={"type": "tournament-teetime-entrant-options", "round_id": round_id},
                        data=entrant_options,
                    )
                    if is_manual
                    else None,
                    html.Div(
                        "Assign Players" if is_manual else "Manage Tee Times",
                        className="t3g-modal-label t3g-tournament-rounds-label mt-2 mb-1",
                    ),
                    html.Div(
                        html.Div([header_row] + slot_rows, className="t3g-teetime-assign-table"),
                        className="t3g-teetime-assign-table-scroll",
                    ),
                    html.Button(
                        "Save Assignments",
                        id={"type": "tournament-teetime-save-assignments", "round_id": round_id},
                        className="t3g-panel-action-button mt-2",
                        n_clicks=0,
                    )
                    if is_manual
                    else None,
                ],
            )

        round_sections.append(
            html.Div(
                className="t3g-modal-section t3g-teetime-round-section",
                children=[
                    html.Div(round_heading, className="t3g-modal-label t3g-tournament-rounds-label"),
                    my_group_section,
                    html.Div(
                        id={"type": "tournament-liveround-error", "round_id": round_id},
                        className="text-danger mb-2",
                    ),
                    # Per-round, same MATCH-key-consistency reasoning as the
                    # rest of this section's per-round Locations -- see
                    # handle_start_live_round.
                    dcc.Location(
                        id={"type": "tournament-liveround-redirect", "round_id": round_id}, refresh=True
                    ),
                    admin_controls,
                    html.Div(
                        id={"type": "tournament-teetime-error", "round_id": round_id},
                        className="text-danger mb-2",
                    ),
                    # Per-round, not a single shared Location -- Dash requires
                    # every Output in a MATCH callback to carry the same
                    # wildcard keys, so the redirect has to be keyed by
                    # round_id right alongside the error div rather than one
                    # page-level component (see handle_generate_tee_times /
                    # handle_save_tee_time_assignments).
                    dcc.Location(
                        id={"type": "tournament-teetime-redirect", "round_id": round_id}, refresh=True
                    ),
                    management_table,
                    # Wrapped in .t3g-teetime-group-list-scroll (not just
                    # the inner .t3g-teetime-group-list card itself) so the
                    # recap table can scroll sideways on mobile once a
                    # round's group_size goes past 3 players, instead of
                    # squeezing every player column narrower and narrower --
                    # see the mobile block for .t3g-teetime-group-list-
                    # scroll / .t3g-teetime-recap-row / .t3g-teetime-recap-
                    # cell in club.css.
                    html.Div(
                        html.Div(group_rows, className="t3g-teetime-group-list"),
                        className="t3g-teetime-group-list-scroll",
                    ),
                ],
            )
        )

    if not round_sections:
        round_sections = [html.P("No rounds set up yet.", className="t3g-empty-state")]

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Tee Times"),
            html.Div(round_sections, className="t3g-panel-body"),
        ],
    )


def _tournament_info_panel(tournament, is_admin):
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

    # Opens tournament-edit-modal (handle_tournament_edit_modal below) --
    # same form as club.py's create-tournament modal, prefilled from this
    # tournament's current data.
    action = (
        html.Button(
            "Edit",
            id="tournament-edit-button",
            className="t3g-panel-action-button t3g-panel-action-button--secondary",
            n_clicks=0,
        )
        if is_admin
        else None
    )

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Tournament Info", action=action),
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


def _entry_toggle_meta(tournament, my_entry):
    """One button that both enters/applies and withdraws, depending on
    the player's current status -- which of those it does is read off
    tournament-entry-action-store by handle_tournament_entry below,
    rather than the button having two identities like the old enter/
    withdraw pair did."""
    entry_mode = tournament.get("entry_mode", "self")
    status = my_entry["status"] if my_entry else None

    if status in ("pending", "confirmed"):
        label = "Withdraw"
        action = "withdraw"
        button_class = "t3g-panel-action-button t3g-panel-action-button--secondary"
    else:
        label = "Enter Tournament" if entry_mode == "self" else "Apply to Enter"
        action = "enter"
        button_class = "t3g-panel-action-button"

    status_message = {
        "pending": "Your application is pending approval.",
        "confirmed": "You're entered in this tournament.",
        "rejected": "Your application wasn't accepted.",
        "withdrawn": "You've withdrawn from this tournament.",
    }.get(status)

    return label, action, button_class, status_message


def _entrants_panel(tournament, entrants, my_entry, is_admin):
    pending = [e for e in entrants if e["status"] == "pending"]
    confirmed = [e for e in entrants if e["status"] == "confirmed"]

    toggle_label, toggle_action, toggle_class, status_message = _entry_toggle_meta(tournament, my_entry)

    action_buttons = [
        html.Button(toggle_label, id="tournament-entry-toggle-button", className=toggle_class, n_clicks=0),
    ]
    if is_admin:
        action_buttons.append(
            html.Button(
                "Add Player",
                id="tournament-add-player-open-button",
                className="t3g-panel-action-button t3g-panel-action-button--secondary",
                n_clicks=0,
            ),
        )

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

    # Same row/list classes as the pending-applications section above --
    # Remove sits inline on each row (admin-only) instead of going through
    # a separate "Remove Player" modal + player-picker dropdown, so it's a
    # single click straight from the row you're looking at.
    if confirmed:
        confirmed_items = [
            html.Div(
                className="t3g-friend-request-row",
                children=[
                    html.Span(
                        _entrant_label(e)
                        + (f" — hcp {e['handicap_at_entry']}" if e.get("handicap_at_entry") is not None else ""),
                        className="t3g-friend-request-name",
                    ),
                    html.Div(
                        html.Button(
                            "Remove",
                            id={"type": "tournament-entrant-remove", "player_id": e["player_id"]},
                            className="t3g-panel-action-button t3g-panel-action-button--secondary",
                            n_clicks=0,
                        ),
                        className="t3g-friend-request-actions",
                    )
                    if is_admin
                    else None,
                ],
            )
            for e in confirmed
        ]
    else:
        confirmed_items = [html.P("No confirmed entrants yet.", className="t3g-empty-state")]

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Entrants", action=action_buttons),
            html.Div(
                className="t3g-panel-body",
                children=[
                    html.P(status_message, className="t3g-empty-state mb-2") if status_message else None,
                    html.Div(id="tournament-entry-error", className="text-danger mb-2"),
                    admin_pending_section,
                    html.Div("Confirmed", className="t3g-modal-label t3g-tournament-rounds-label mt-2 mb-1"),
                    html.Div(id="tournament-remove-entrant-error", className="text-danger mb-2"),
                    html.Div(confirmed_items, className="t3g-friend-request-list"),
                ],
            ),
        ],
    )


_LEADERBOARD_FORMAT_KEYS = ("gross", "stableford", "nett")
_LEADERBOARD_FORMAT_LABELS = {"gross": "Gross", "stableford": "Stableford", "nett": "Nett"}
# Tournament format -> which of the leaderboard's three display modes it
# opens on by default. Team formats (2bbb/4bbb/texas_scramble) don't map
# onto an individual per-player leaderboard the way this table works, so
# they fall back to Gross same as "scratch" rather than guessing.
_TOURNAMENT_FORMAT_TO_LEADERBOARD_MODE = {"stableford": "stableford", "net": "nett"}

_LEADERBOARD_REFRESH_INTERVAL_MS = 25_000

# Detailed = the existing hole-by-hole Masters-style grid (_leaderboard_
# table). Simple = just Pos/Player/Total/Today (_leaderboard_simple_table)
# -- same sort order, same Gross/Stableford/Nett format toggle, same
# click-a-row-for-their-scorecard interaction, just far fewer columns for
# someone who only wants "who's winning" at a glance instead of a full
# scorecard grid. Detailed is the default -- it's what's always been here.
_LEADERBOARD_VIEW_KEYS = ("detailed", "simple")
_LEADERBOARD_VIEW_LABELS = {"detailed": "Detailed", "simple": "Simple"}


def _default_leaderboard_mode(tournament):
    return _TOURNAMENT_FORMAT_TO_LEADERBOARD_MODE.get(tournament.get("format"), "gross")


def _round_has_grouping_status(r, live_status):
    return any((g.get("live_round") or {}).get("status") == live_status for g in r.get("tee_times", []))


def _default_leaderboard_round(tournament):
    """Which round the leaderboard opens on -- whatever's actively being
    played (highest round_number with a group still in_progress) takes
    priority, since that's what "live" means here; failing that, the
    latest round with anything completed; failing that, just Round 1 so
    there's always something selected before a single shot's been hit."""
    rounds = sorted(tournament.get("rounds", []), key=lambda r: r["round_number"])
    if not rounds:
        return None
    in_progress = [r for r in rounds if _round_has_grouping_status(r, "in_progress")]
    if in_progress:
        return in_progress[-1]
    completed = [r for r in rounds if _round_has_grouping_status(r, "completed")]
    if completed:
        return completed[-1]
    return rounds[0]


def _leaderboard_format_classes(active_mode):
    return {
        key: ("t3g-leaderboard-format-tab t3g-leaderboard-format-tab--active" if key == active_mode
              else "t3g-leaderboard-format-tab")
        for key in _LEADERBOARD_FORMAT_KEYS
    }


def _leaderboard_view_classes(active_view):
    # Reuses the exact same pill-toggle classes as the format tabs
    # (t3g-leaderboard-format-tab[s]) rather than a parallel set of "view"
    # classes -- it's the same visual pattern (a small group of mutually-
    # exclusive flat pills) just applied to a different choice, so there's
    # nothing view-specific to actually style differently.
    return {
        key: ("t3g-leaderboard-format-tab t3g-leaderboard-format-tab--active" if key == active_view
              else "t3g-leaderboard-format-tab")
        for key in _LEADERBOARD_VIEW_KEYS
    }


def _leaderboard_round_classes(round_keys, active_key):
    # Same reused pill-tab classes again -- round_keys isn't a fixed tuple
    # like the view/format ones (it's "overall" plus however many rounds
    # this tournament actually has), so this takes the key list as an
    # argument instead of a module-level constant.
    return {
        key: ("t3g-leaderboard-format-tab t3g-leaderboard-format-tab--active" if key == active_key
              else "t3g-leaderboard-format-tab")
        for key in round_keys
    }


def _leaderboard_panel(tournament):
    """Live, whole-field Masters-style leaderboard -- a round selector (one
    round tab/dropdown per tournament round) and a Gross/Stableford/Nett
    format toggle sit above the table; the table itself (round selector's
    own data, format's own column) is filled in by render_tournament_
    leaderboard_table below once tournament-leaderboard-store has data,
    not built here at layout time, so switching rounds/format never needs
    a full page reload -- just a fetch (round change) or a client-side
    re-render (format change, no new request since the backend already
    computes all three formats at once). tournament-leaderboard-refresh-
    interval polls the currently selected round on a timer so scores
    update on their own while someone's sitting on this tab watching a
    live round."""
    rounds = sorted(tournament.get("rounds", []), key=lambda r: r["round_number"])

    if not rounds:
        return html.Div(
            className="t3g-panel",
            children=[
                build_panel_navbar("Leaderboard"),
                html.Div(html.P("No rounds set up yet.", className="t3g-empty-state"), className="t3g-panel-body"),
            ],
        )

    default_mode = _default_leaderboard_mode(tournament)
    format_classes = _leaderboard_format_classes(default_mode)
    default_view = "detailed"
    view_classes = _leaderboard_view_classes(default_view)

    # "Overall" is a sentinel, not a real round id -- it stands for
    # "whichever round is currently the tournament's leading edge" (see
    # _default_leaderboard_round: the highest round_number with a group
    # still in_progress, failing that the latest with anything completed,
    # failing that Round 1), resolved for real inside load_tournament_
    # leaderboard rather than pinned to one round_id up front here. It's
    # the default tab -- this is exactly what the round selector used to
    # silently open on before it became explicit/reselectable tabs, so
    # opening the leaderboard behaves the same as it always did, it's just
    # now a named, clickable-back-to option instead of only ever being the
    # initial pick.
    default_round_key = "overall"
    round_keys = ["overall"] + [r["id"] for r in rounds]
    round_classes = _leaderboard_round_classes(round_keys, default_round_key)

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Leaderboard"),
            html.Div(
                className="t3g-panel-body",
                children=[
                    html.Div(
                        className="t3g-leaderboard-controls",
                        children=[
                            html.Div(
                                className="t3g-leaderboard-format-tabs t3g-leaderboard-round-tabs",
                                children=[
                                    html.Button(
                                        "Overall",
                                        id={"type": "tournament-leaderboard-round-button", "round_id": "overall"},
                                        className=round_classes["overall"],
                                        n_clicks=0,
                                    ),
                                    *[
                                        html.Button(
                                            f"Round {r['round_number']}",
                                            id={"type": "tournament-leaderboard-round-button", "round_id": r["id"]},
                                            className=round_classes[r["id"]],
                                            n_clicks=0,
                                        )
                                        for r in rounds
                                    ],
                                ],
                            ),
                            html.Div(
                                className="t3g-leaderboard-format-tabs",
                                children=[
                                    html.Button(
                                        _LEADERBOARD_VIEW_LABELS[key],
                                        id={"type": "tournament-leaderboard-view-button", "view": key},
                                        className=view_classes[key],
                                        n_clicks=0,
                                    )
                                    for key in _LEADERBOARD_VIEW_KEYS
                                ],
                            ),
                            html.Div(
                                className="t3g-leaderboard-format-tabs",
                                children=[
                                    html.Button(
                                        _LEADERBOARD_FORMAT_LABELS[key],
                                        id={"type": "tournament-leaderboard-format-button", "mode": key},
                                        className=format_classes[key],
                                        n_clicks=0,
                                    )
                                    for key in _LEADERBOARD_FORMAT_KEYS
                                ],
                            ),
                        ],
                    ),
                    html.Div(id="tournament-leaderboard-error", className="text-danger mb-2"),
                    dcc.Loading(
                        html.Div(id="tournament-leaderboard-table-container"),
                        custom_spinner=golf_swing_spinner(),
                    ),
                    dcc.Store(id="tournament-leaderboard-store"),
                    dcc.Store(id="tournament-leaderboard-format-store", data=default_mode),
                    dcc.Store(id="tournament-leaderboard-view-store", data=default_view),
                    dcc.Store(id="tournament-leaderboard-round-store", data=default_round_key),
                    # A snapshot of this tournament's own rounds (with their
                    # tee_times/live_round statuses already embedded, same
                    # shape _default_leaderboard_round expects) -- what
                    # load_tournament_leaderboard resolves "overall" against.
                    # Taken once at page load, same as the round selector's
                    # old default value always was -- a group starting a
                    # brand new round after this page is already open won't
                    # retroactively move "Overall" onto it without a refresh,
                    # but that's no different from how the previous default
                    # selection behaved either.
                    dcc.Store(id="tournament-leaderboard-rounds-store", data=rounds),
                    dcc.Interval(
                        id="tournament-leaderboard-refresh-interval",
                        interval=_LEADERBOARD_REFRESH_INTERVAL_MS,
                        n_intervals=0,
                    ),
                    _leaderboard_scorecard_modal(),
                ],
            ),
        ],
    )


def _leaderboard_scorecard_modal():
    """Opened by clicking a player's row in the leaderboard table (see
    toggle_tournament_leaderboard_scorecard_modal) -- their Hole/Par/Score
    line for whichever round the leaderboard is currently showing. Body
    content is filled in by the callback, not here, since it depends on
    which row got clicked."""
    return dbc.Modal(
        id="tournament-leaderboard-scorecard-modal",
        is_open=False,
        size="xl",
        children=[
            dbc.ModalHeader(dbc.ModalTitle(id="tournament-leaderboard-scorecard-modal-title")),
            dbc.ModalBody(id="tournament-leaderboard-scorecard-modal-body"),
            dbc.ModalFooter(dbc.Button("Close", id="tournament-leaderboard-scorecard-close", color="secondary")),
        ],
    )


def _leaderboard_player_scorecard(player, holes):
    """Read-only Hole/Par/Score line for one player's selected round --
    built straight from holes_strokes, the same raw per-hole strokes the
    leaderboard's cumulative-to-par columns were derived from, so this
    always matches exactly what the grid is already showing rather than
    needing its own fetch.

    holes_nr (parallel to holes_strokes -- see _compute_leaderboard_line
    in backend/services/tournaments.py) is what lets the Score row show
    "NR" specifically on whichever hole(s) this player marked No Return,
    instead of just the same blank "-" a hole they simply haven't reached
    yet would show -- both have strokes=None, only holes_nr tells them
    apart."""
    strokes = player.get("holes_strokes") or [None] * 18
    nr_flags = player.get("holes_nr") or [False] * 18
    pars = [h.get("par") for h in holes]
    hole_numbers = [h["hole_number"] for h in holes]

    def _row(label, values, bold=False):
        cells = [html.Td(label, className="t3g-leaderboard-scorecard-label")]
        for i, v in enumerate(values):
            divider = " t3g-leaderboard-divider" if hole_numbers[i] == 10 else ""
            cells.append(html.Td(str(v) if v is not None else "-", className=divider.strip()))
        return html.Tr(cells, className="t3g-leaderboard-scorecard-score-row" if bold else None)

    def _score_row(label, values, nr_values):
        cells = [html.Td(label, className="t3g-leaderboard-scorecard-label")]
        for i, v in enumerate(values):
            divider = " t3g-leaderboard-divider" if hole_numbers[i] == 10 else ""
            is_nr_hole = nr_values[i] if i < len(nr_values) else False
            text = "NR" if is_nr_hole else (str(v) if v is not None else "-")
            cls = (divider + (" t3g-leaderboard-scorecard-nr-cell" if is_nr_hole else "")).strip()
            cells.append(html.Td(text, className=cls))
        return html.Tr(cells, className="t3g-leaderboard-scorecard-score-row")

    header_cells = [html.Th("Hole")]
    for hole_number in hole_numbers:
        divider = " t3g-leaderboard-divider" if hole_number == 10 else ""
        header_cells.append(html.Th(str(hole_number), className=divider.strip()))

    out_par = sum(p for p in pars[:9] if p is not None)
    in_par = sum(p for p in pars[9:] if p is not None)
    out_strokes = [s for s in strokes[:9] if s is not None]
    in_strokes = [s for s in strokes[9:] if s is not None]
    total_strokes = out_strokes + in_strokes

    summary = html.Div(
        className="t3g-leaderboard-scorecard-summary",
        children=[
            html.Span(f"OUT  {sum(out_strokes) if out_strokes else '-'}  (par {out_par})"),
            html.Span(f"IN  {sum(in_strokes) if in_strokes else '-'}  (par {in_par})"),
            html.Span(
                f"TOTAL  {sum(total_strokes) if total_strokes else '-'}  (par {out_par + in_par})",
                className="t3g-leaderboard-scorecard-total",
            ),
        ],
    )

    return html.Div(
        [
            html.Div(
                html.Table(
                    [
                        html.Thead(html.Tr(header_cells)),
                        html.Tbody([_row("Par", pars), _score_row("Score", strokes, nr_flags)]),
                    ],
                    className="t3g-leaderboard-table t3g-leaderboard-scorecard-table",
                ),
                className="t3g-leaderboard-wrap",
            ),
            summary,
        ]
    )


def _leaderboard_to_par_text(value):
    if value is None:
        return "–"
    if value == 0:
        return "E"
    return f"+{value}" if value > 0 else str(value)


def _leaderboard_cell_class(value, mode):
    base = "t3g-leaderboard-cell"
    if value is None:
        return f"{base} t3g-leaderboard-cell-empty"
    if mode != "stableford" and value < 0:
        return f"{base} t3g-leaderboard-cell-under"
    return base


def _leaderboard_total_pill_class(value, mode):
    """Modifier for the Total column's pill -- red/good, grey/level, navy/
    over for gross+nett (same under-par-is-red convention as the hole
    cells, just promoted to a flat chip since Total is the one number
    someone actually scans the whole board for). Stableford has no
    under/over polarity to key off -- just points -- so it always gets the
    same neutral "points" chip instead."""
    if mode == "stableford":
        return "t3g-leaderboard-total-pill t3g-leaderboard-total-pill--points"
    if value is None:
        return "t3g-leaderboard-total-pill t3g-leaderboard-total-pill--even"
    if value < 0:
        return "t3g-leaderboard-total-pill t3g-leaderboard-total-pill--under"
    if value == 0:
        return "t3g-leaderboard-total-pill t3g-leaderboard-total-pill--even"
    return "t3g-leaderboard-total-pill t3g-leaderboard-total-pill--over"


def _leaderboard_initials(name):
    words = [w for w in name.split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def _leaderboard_avatar(name, photo_url):
    """The circle at the start of every leaderboard row -- the player's
    real profile picture once they've uploaded one (photo_url comes
    straight through from get_tournament_leaderboard, which now carries
    it off the same players(...) embed _fetch_entrants_by_tournament
    already fetches), falling back to the initials badge otherwise
    exactly like before. Same t3g-leaderboard-avatar sizing/circle
    either way -- the photo variant just adds --photo for object-fit:
    cover (see club.css, shared globally) so a non-square upload doesn't
    stretch. Same helper, same reasoning, as club.py's own
    _leaderboard_avatar -- duplicated rather than imported per this
    app's usual per-page convention."""
    if photo_url:
        return html.Img(
            src=photo_url,
            alt="",
            className="t3g-leaderboard-avatar t3g-leaderboard-avatar--photo",
        )
    return html.Span(_leaderboard_initials(name), className="t3g-leaderboard-avatar")


def _leaderboard_positions(sorted_players, total_key):
    """Sequential rank with ties sharing a position (and a "T" prefix once
    they do) -- same convention every real leaderboard uses rather than
    just numbering rows 1..N regardless of equal scores.

    Any player flagged is_nr (see get_tournament_leaderboard) gets "NR"
    instead of a rank -- a No Return card isn't compared against anyone
    else's score, it just always sorts after every real one (see the
    mode-aware sort key in _leaderboard_table/_leaderboard_simple_table,
    which guarantees every NR player is already at the end of sorted_
    players by the time this runs, so they never affect a real player's
    tie-detection either)."""
    positions = []
    real_rank = 0
    prev_total = None
    for p in sorted_players:
        if p.get("is_nr"):
            positions.append("NR")
            continue
        real_rank += 1
        rank = positions[-1] if (prev_total is not None and p[total_key] == prev_total and positions and positions[-1] != "NR") else real_rank
        positions.append(rank)
        prev_total = p[total_key]

    counts: dict[int, int] = {}
    for pos in positions:
        if pos != "NR":
            counts[pos] = counts.get(pos, 0) + 1
    return [pos if pos == "NR" else (f"T{pos}" if counts[pos] > 1 else str(pos)) for pos in positions]


def _leaderboard_table(leaderboard_data, mode):
    holes = leaderboard_data.get("holes", [])
    players = leaderboard_data.get("players", [])

    if not players:
        return html.P("No confirmed entrants yet.", className="t3g-empty-state")

    holes_key = f"holes_{mode}"
    prior_key = f"prior_{mode}"
    total_key = f"total_{mode}"

    # Gross/Nett: lowest to-par leads. Stableford: most points leads. Any
    # player flagged is_nr always sorts after every real score regardless
    # of mode -- the (is_nr, ...) tuple key does that in one pass instead
    # of sorting normally and then re-shuffling NR rows afterward; negating
    # the stableford value inside the tuple is what keeps "highest points
    # first" true under an otherwise-ascending sort (reverse=True alone
    # would also reverse the is_nr half of the tuple, putting NR players
    # FIRST for stableford specifically, which is exactly backwards).
    sorted_players = sorted(
        players,
        key=lambda p: (p.get("is_nr", False), -p[total_key] if mode == "stableford" else p[total_key]),
    )
    positions = _leaderboard_positions(sorted_players, total_key)

    value_text = (lambda v: str(v)) if mode == "stableford" else _leaderboard_to_par_text

    # Two header rows, same idea as a printed scorecard: hole numbers on
    # top, par directly beneath each one -- Pos/Player/Total/Thru/Prior
    # only need to be labelled once, on the hole-number row.
    hole_header_row = [html.Th(""), html.Th("Player"), html.Th("Total"), html.Th("Thru"), html.Th("Prior")]
    par_header_row = [html.Th(""), html.Th(""), html.Th(""), html.Th(""), html.Th("Par")]
    for hole in holes:
        divider = " t3g-leaderboard-divider" if hole["hole_number"] == 10 else ""
        hole_header_row.append(html.Th(str(hole["hole_number"]), className=divider.strip()))
        par_header_row.append(
            html.Th(str(hole["par"]) if hole.get("par") is not None else "-", className=divider.strip())
        )

    body_rows = []
    for pos, p in zip(positions, sorted_players):
        is_nr = bool(p.get("is_nr"))
        is_finished = p["thru"] == 18
        thru_text = "F" if is_finished else (str(p["thru"]) if p["thru"] else "–")

        # Tier comes from the *displayed* position (so a tie for 1st -- "T1"
        # -- still reads as top-tier for both rows), not row order, which
        # would only ever flag the first of a tied pair. pos is the literal
        # string "NR" for an is_nr row (see _leaderboard_positions) -- no
        # tier to compute there, int("NR") would just raise.
        tier = None if pos == "NR" else int(pos.lstrip("T"))
        tier_class = {1: " t3g-leaderboard-pos-badge--first",
                      2: " t3g-leaderboard-pos-badge--second",
                      3: " t3g-leaderboard-pos-badge--third"}.get(tier, "")

        total_cell = (
            html.Span("NR", className="t3g-leaderboard-total-pill t3g-leaderboard-total-pill--nr")
            if is_nr
            else html.Span(value_text(p[total_key]), className=_leaderboard_total_pill_class(p[total_key], mode))
        )

        row_cells = [
            html.Td(
                html.Span(pos, className="t3g-leaderboard-pos-badge" + tier_class),
                className="t3g-leaderboard-pos",
            ),
            html.Td(
                html.Div(
                    [
                        _leaderboard_avatar(p["name"], p.get("photo_url")),
                        html.Span(p["name"]),
                    ],
                    className="t3g-leaderboard-player-cell",
                ),
                className="t3g-leaderboard-player-col",
            ),
            html.Td(total_cell, className="t3g-leaderboard-total-col"),
            html.Td(
                html.Span(thru_text, className="t3g-leaderboard-thru-badge" + (" t3g-leaderboard-thru-badge--finished" if is_finished else ""))
            ),
            html.Td(value_text(p[prior_key]), className="t3g-leaderboard-prior-col"),
        ]
        for i, cell_value in enumerate(p[holes_key]):
            hole_number = holes[i]["hole_number"]
            divider = " t3g-leaderboard-divider" if hole_number == 10 else ""
            cell_text = "–" if cell_value is None else (str(cell_value) if mode == "stableford" else _leaderboard_to_par_text(cell_value))
            row_cells.append(
                html.Td(cell_text, className=(_leaderboard_cell_class(cell_value, mode) + divider))
            )
        body_rows.append(
            html.Tr(
                row_cells,
                id={"type": "tournament-leaderboard-player-row", "player_id": p["player_id"]},
                n_clicks=0,
                # Position 1 (or every row tied for it) gets its own
                # highlight on top of the usual clickable-row treatment --
                # the leader should read as "the leader" at a glance. An
                # NR row gets its own muted treatment instead -- it's
                # still clickable (same scorecard modal, showing whatever
                # partial card they had before NR-ing).
                className="t3g-leaderboard-row" + (" t3g-leaderboard-row--leader" if tier == 1 else "") + (" t3g-leaderboard-row--nr" if is_nr else ""),
            )
        )

    return html.Div(
        html.Table(
            [
                html.Thead([html.Tr(hole_header_row), html.Tr(par_header_row, className="t3g-leaderboard-par-row")]),
                html.Tbody(body_rows),
            ],
            className="t3g-leaderboard-table",
        ),
        className="t3g-leaderboard-wrap",
    )


def _leaderboard_today_cell(p, mode, total_key, prior_key, value_text):
    """The Simple view's 4th column -- this round's own score once it's
    finished (total minus prior, since both are cumulative-to-par/points
    sums and to-par/points are additive across rounds, so the difference
    is exactly what this one round contributed on its own), or how far
    through the round they are otherwise. Reuses the exact same total-pill
    styling as the Total column once finished -- it's the same kind of
    number (a to-par score or a points total), just scoped to one round
    instead of the whole tournament -- and the same thru-badge styling the
    detailed table uses for "not finished yet" everywhere else.

    is_nr short-circuits straight to an "NR" pill -- the total-minus-prior
    subtraction above isn't meaningful once a round's card is void, and a
    thru count doesn't tell you anything useful either at that point."""
    if p.get("is_nr"):
        return html.Span("NR", className="t3g-leaderboard-total-pill t3g-leaderboard-total-pill--nr")

    if p["thru"] == 18:
        round_value = p[total_key] - p[prior_key]
        return html.Span(value_text(round_value), className=_leaderboard_total_pill_class(round_value, mode))

    thru_text = f"Thru {p['thru']}" if p["thru"] else "–"
    return html.Span(thru_text, className="t3g-leaderboard-thru-badge")


def _leaderboard_simple_table(leaderboard_data, mode, clickable=True):
    """Same players, same sort order, same Gross/Stableford/Nett format as
    _leaderboard_table above -- just Pos/Player/Total/Today instead of the
    full hole-by-hole grid, for someone who wants "who's winning" at a
    glance rather than a full scorecard.

    clickable=True (the Leaderboard tab's own Simple view) gives rows the
    same id pattern (tournament-leaderboard-player-row) the detailed table
    uses, so toggle_tournament_leaderboard_scorecard already handles
    clicks from either view without any changes -- it matches on an ALL
    pattern, not which table rendered the row. clickable=False (the
    compact panel embedded in the Tournament Info tab, see
    _tournament_info_leaderboard_panel) deliberately leaves rows inert --
    that panel has no scorecard modal of its own, and wiring its rows into
    the *other* tab's modal would show whichever round the Leaderboard tab
    happens to be sitting on, not necessarily this panel's own (always-
    latest) round -- a real mismatch, not just an unnecessary feature."""
    players = leaderboard_data.get("players", [])

    if not players:
        return html.P("No confirmed entrants yet.", className="t3g-empty-state")

    total_key = f"total_{mode}"
    prior_key = f"prior_{mode}"

    # Same NR-aware sort as _leaderboard_table -- see that function's
    # comment for why the tuple key (not a plain reverse=) is what's
    # needed to keep NR players last regardless of mode.
    sorted_players = sorted(
        players,
        key=lambda p: (p.get("is_nr", False), -p[total_key] if mode == "stableford" else p[total_key]),
    )
    positions = _leaderboard_positions(sorted_players, total_key)
    value_text = (lambda v: str(v)) if mode == "stableford" else _leaderboard_to_par_text

    header_row = [html.Th(""), html.Th("Player"), html.Th("Total"), html.Th("Today")]

    body_rows = []
    for pos, p in zip(positions, sorted_players):
        is_nr = bool(p.get("is_nr"))
        # pos is the literal string "NR" for an is_nr row (see
        # _leaderboard_positions) -- no tier to compute there.
        tier = None if pos == "NR" else int(pos.lstrip("T"))
        tier_class = {1: " t3g-leaderboard-pos-badge--first",
                      2: " t3g-leaderboard-pos-badge--second",
                      3: " t3g-leaderboard-pos-badge--third"}.get(tier, "")

        total_cell = (
            html.Span("NR", className="t3g-leaderboard-total-pill t3g-leaderboard-total-pill--nr")
            if is_nr
            else html.Span(value_text(p[total_key]), className=_leaderboard_total_pill_class(p[total_key], mode))
        )

        row_kwargs = {}
        row_class = (" t3g-leaderboard-row--leader" if tier == 1 else "") + (" t3g-leaderboard-row--nr" if is_nr else "")
        if clickable:
            row_kwargs["id"] = {"type": "tournament-leaderboard-player-row", "player_id": p["player_id"]}
            row_kwargs["n_clicks"] = 0
            row_class = "t3g-leaderboard-row" + row_class
        else:
            row_class = row_class.strip()

        body_rows.append(
            html.Tr(
                [
                    html.Td(
                        html.Span(pos, className="t3g-leaderboard-pos-badge" + tier_class),
                        className="t3g-leaderboard-pos",
                    ),
                    html.Td(
                        html.Div(
                            [
                                _leaderboard_avatar(p["name"], p.get("photo_url")),
                                html.Span(p["name"]),
                            ],
                            className="t3g-leaderboard-player-cell",
                        ),
                        className="t3g-leaderboard-player-col",
                    ),
                    html.Td(total_cell, className="t3g-leaderboard-total-col"),
                    html.Td(_leaderboard_today_cell(p, mode, total_key, prior_key, value_text)),
                ],
                className=row_class,
                **row_kwargs,
            )
        )

    return html.Div(
        html.Table(
            [html.Thead(html.Tr(header_row)), html.Tbody(body_rows)],
            className="t3g-leaderboard-table t3g-leaderboard-table--simple",
        ),
        className="t3g-leaderboard-wrap",
    )


def _tournament_info_leaderboard_panel(tournament):
    """Compact "how's it going" leaderboard for the Tournament Info tab --
    always the Simple 4-column view (see _leaderboard_simple_table),
    always whichever round is the tournament's current leading edge (same
    resolution _default_leaderboard_round computes for the full
    Leaderboard tab's Overall tab -- highest round_number with a group
    still in_progress, failing that the latest with anything completed,
    failing that Round 1 -- just pinned once here at page-load time rather
    than offered as a reselectable tab, since there's no round picker in
    this compact panel). The Gross/Stableford/Nett toggle still works
    (own store/callback, separate from the Leaderboard tab's), defaulting
    to whichever of those this tournament's own format maps onto -- same
    default the full Leaderboard tab opens on (see _default_leaderboard_
    mode) -- not hardcoded to Gross.

    Sits side-by-side with Tournament Info in a two-column grid (see
    layout()'s tournament-tab-panel-info children), so the current
    standings are visible on the tab someone lands on by default, without
    switching to Leaderboard. Deliberately lighter than the full panel:
    no round tabs, no Detailed option, no click-through scorecard modal --
    just the numbers."""
    rounds = tournament.get("rounds", [])

    if not rounds:
        return html.Div(
            className="t3g-panel",
            children=[
                build_panel_navbar("Leaderboard"),
                html.Div(html.P("No rounds set up yet.", className="t3g-empty-state"), className="t3g-panel-body"),
            ],
        )

    default_round = _default_leaderboard_round(tournament)
    default_mode = _default_leaderboard_mode(tournament)
    format_classes = _leaderboard_format_classes(default_mode)

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Leaderboard"),
            html.Div(
                className="t3g-panel-body",
                children=[
                    html.Div(
                        className="t3g-leaderboard-controls",
                        children=[
                            html.Div(
                                className="t3g-leaderboard-format-tabs",
                                children=[
                                    html.Button(
                                        _LEADERBOARD_FORMAT_LABELS[key],
                                        id={"type": "tournament-info-leaderboard-format-button", "mode": key},
                                        className=format_classes[key],
                                        n_clicks=0,
                                    )
                                    for key in _LEADERBOARD_FORMAT_KEYS
                                ],
                            ),
                        ],
                    ),
                    html.Div(id="tournament-info-leaderboard-error", className="text-danger mb-2"),
                    # t3g-leaderboard-compact scopes a denser type scale
                    # (see club.css) to just this panel's table -- the
                    # full Leaderboard tab's own Detailed/Simple tables
                    # share the same base .t3g-leaderboard-* classes and
                    # stay at their normal size, since this panel is the
                    # one squeezed into half a two-column row rather than
                    # the whole tab. parent_className (not className --
                    # that one only targets dcc.Loading's own spinner
                    # element) is what actually lands on the wrapper div
                    # around the children, which is what a descendant
                    # selector needs to reach the rendered table inside.
                    dcc.Loading(
                        html.Div(id="tournament-info-leaderboard-table-container"),
                        custom_spinner=golf_swing_spinner(),
                        parent_className="t3g-leaderboard-compact",
                    ),
                    dcc.Store(id="tournament-info-leaderboard-store"),
                    dcc.Store(id="tournament-info-leaderboard-format-store", data=default_mode),
                    # A concrete round_id, resolved once at build time --
                    # unlike the Leaderboard tab's round-store, this panel
                    # has no "overall" sentinel to re-resolve later, since
                    # there's nothing here that would ever set it to
                    # anything else.
                    dcc.Store(
                        id="tournament-info-leaderboard-round-store",
                        data=default_round["id"] if default_round else None,
                    ),
                    dcc.Interval(
                        id="tournament-info-leaderboard-refresh-interval",
                        interval=_LEADERBOARD_REFRESH_INTERVAL_MS,
                        n_intervals=0,
                    ),
                ],
            ),
        ],
    )


def _edit_round_row(index, prefill=None):
    """One row of the Edit Tournament modal's round list -- same shape as
    club.py's _tournament_round_row, "tournament-edit-" ids instead of
    "tournament-" so this page's callbacks never collide with club.py's.

    Course and tee both start with just their currently-selected option
    (built from the club_name/course_name/tee_name tournament.rounds
    already came with) instead of the full list -- search_tournament_edit_
    round_course_options below fills course in as you type instead, same
    "don't preload the whole catalog" fix club.py's create modal and
    home.py's round-upload picker already have; the tee list still comes
    from load_tournament_edit_round_tees once a course is actually picked
    or changed. Without seeding at least the current selection, both
    dropdowns would render blank on open despite value being set -- same
    "blank after selection" issue those other fixes address."""
    prefill = prefill or {}
    round_date = prefill.get("round_date")
    course_id = prefill.get("course_id")
    club_name = prefill.get("club_name")
    course_name = prefill.get("course_name")
    tee_id = prefill.get("tee_id")
    tee_name = prefill.get("tee_name")
    group_size = prefill.get("group_size") or _DEFAULT_GROUP_SIZE

    course_label = club_name or course_name
    if club_name and course_name:
        course_label = f"{club_name} — {course_name}"
    course_options = [{"label": course_label, "value": course_id}] if course_id and course_label else []
    tee_options = [{"label": f"{tee_name} tees", "value": tee_id}] if tee_id and tee_name else []

    return html.Div(
        id={"type": "tournament-edit-round-row", "index": index},
        className="t3g-tournament-round-row",
        children=[
            html.Span(f"Round {index + 1}", className="t3g-tournament-round-number"),
            dcc.DatePickerSingle(
                id={"type": "tournament-edit-round-date", "index": index},
                date=round_date,
                placeholder="Date",
                display_format="D MMM YYYY",
                className="t3g-tournament-round-date",
            ),
            dcc.Dropdown(
                id={"type": "tournament-edit-round-course", "index": index},
                options=course_options,
                value=course_id,
                placeholder="Search course...",
                searchable=True,
                className="t3g-tournament-round-course",
            ),
            dcc.Dropdown(
                id={"type": "tournament-edit-round-tee", "index": index},
                options=tee_options,
                value=tee_id,
                placeholder="Tees",
                disabled=not tee_id,
                className="t3g-tournament-round-tee",
            ),
            dcc.Dropdown(
                id={"type": "tournament-edit-round-group-size", "index": index},
                options=_GROUP_SIZE_OPTIONS,
                value=group_size,
                clearable=False,
                className="t3g-tournament-round-group-size",
            ),
            html.Button(
                "Remove",
                id={"type": "tournament-edit-round-remove", "index": index},
                className="t3g-panel-action-button t3g-panel-action-button--secondary t3g-tournament-round-remove",
                n_clicks=0,
            ),
        ],
    )


def _handicap_stepper(id_prefix, label, initial_value=None):
    """Same +/- stepper as club.py's copy -- see that function's docstring
    -- except this one can start pre-filled (initial_value) rather than
    always starting unset, since the edit modal opens with the
    tournament's existing min/max handicap already set."""
    return html.Div(
        className="t3g-stepper-col",
        children=[
            html.Div(label, className="t3g-stepper-label"),
            html.Div(
                className="t3g-stepper t3g-stepper--horizontal",
                children=[
                    html.Button("–", id=f"{id_prefix}-minus", className="t3g-stepper-button", n_clicks=0),
                    html.Div(
                        str(initial_value) if initial_value is not None else "–",
                        id=f"{id_prefix}-display",
                        className="t3g-stepper-value",
                    ),
                    html.Button("+", id=f"{id_prefix}-plus", className="t3g-stepper-button", n_clicks=0),
                ],
            ),
            dcc.Store(id=f"{id_prefix}-store", data=initial_value),
        ],
    )


def _tournament_edit_modal(tournament):
    rounds = tournament.get("rounds", [])
    round_rows = [_edit_round_row(i, r) for i, r in enumerate(rounds)] or [_edit_round_row(0)]

    return dbc.Modal(
        id="tournament-edit-modal",
        is_open=False,
        size="lg",
        children=[
            dbc.ModalHeader(dbc.ModalTitle("Edit Tournament")),
            dbc.ModalBody(
                children=[
                    html.Div(
                        className="t3g-modal-section",
                        children=[
                            dbc.Input(
                                id="tournament-edit-name-input",
                                placeholder="Tournament name",
                                type="text",
                                value=tournament.get("name"),
                                className="mb-2",
                            ),
                            dcc.Dropdown(
                                id="tournament-edit-format-input",
                                options=_TOURNAMENT_FORMAT_OPTIONS,
                                value=tournament.get("format"),
                                placeholder="Format",
                            ),
                        ],
                    ),
                    html.Div(
                        className="t3g-modal-section-row",
                        children=[
                            html.Div(
                                className="t3g-modal-section",
                                children=[
                                    html.Label(
                                        "Who can enter", className="t3g-modal-label t3g-tournament-rounds-label"
                                    ),
                                    dcc.RadioItems(
                                        id="tournament-edit-entry-mode-input",
                                        options=_TOURNAMENT_ENTRY_MODE_OPTIONS,
                                        value=tournament.get("entry_mode", "self"),
                                        className="t3g-tournament-entry-mode",
                                    ),
                                ],
                            ),
                            html.Div(
                                className="t3g-modal-section",
                                children=[
                                    html.Label("Handicap range (optional)", className="t3g-modal-label"),
                                    html.Div(
                                        className="t3g-stepper-row",
                                        children=[
                                            _handicap_stepper(
                                                "tournament-edit-min-handicap", "Min", tournament.get("min_handicap")
                                            ),
                                            _handicap_stepper(
                                                "tournament-edit-max-handicap", "Max", tournament.get("max_handicap")
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        className="t3g-modal-section",
                        children=[
                            html.Label(
                                "Tee time grouping", className="t3g-modal-label t3g-tournament-rounds-label"
                            ),
                            dcc.RadioItems(
                                id="tournament-edit-grouping-method-input",
                                options=_TOURNAMENT_GROUPING_METHOD_OPTIONS,
                                value=tournament.get("grouping_method", "random"),
                                className="t3g-tournament-entry-mode",
                            ),
                        ],
                    ),
                    html.Div(
                        className="t3g-modal-section",
                        children=[
                            html.Label("Rounds", className="t3g-modal-label t3g-tournament-rounds-label"),
                            html.Div(id="tournament-edit-rounds-container", children=round_rows),
                            html.Button(
                                "+ Add Round",
                                id="tournament-edit-add-round",
                                className="t3g-panel-action-button t3g-panel-action-button--secondary mt-2",
                                n_clicks=0,
                            ),
                        ],
                    ),
                    html.Div(id="tournament-edit-error", className="text-danger mt-3"),
                ],
            ),
            dbc.ModalFooter(
                [
                    dbc.Button("Cancel", id="tournament-edit-cancel", color="secondary"),
                    dbc.Button("Save Changes", id="tournament-edit-submit", color="primary"),
                ]
            ),
        ],
    )


_TAB_BUTTON_BASE = "t3g-tournament-tab"
_TAB_BUTTON_ACTIVE = "t3g-tournament-tab t3g-tournament-tab--active"

# Maps a ?tab= query value (also what the Live Round page's own back-to-
# tournament subnav links use, see live_round.py's _tournament_context_
# subnav) to which of the four tab keys -- info/startsheet/leaderboard/
# liveround, in this fixed order -- should be shown/active on first load.
# Anything unrecognized (including no ?tab= at all) falls back to "info",
# same as before this existed.
_TOURNAMENT_TAB_KEYS = ("info", "startsheet", "leaderboard", "liveround")


def _tab_visibility(active_tab):
    """(styles, classes) for all four tab panels/buttons at page-load time,
    picked from a plain ?tab= query value instead of always defaulting to
    Tournament Info -- lets a link from elsewhere (the Live Round page's
    subnav) open straight onto the right tab. switch_tournament_tab still
    owns in-page click-driven switching after that; this is only about
    what the very first render looks like."""
    hidden = {"display": "none"}
    shown = {}
    key = active_tab if active_tab in _TOURNAMENT_TAB_KEYS else "info"
    index = _TOURNAMENT_TAB_KEYS.index(key)

    styles = tuple(shown if i == index else hidden for i in range(4))
    classes = tuple(_TAB_BUTTON_ACTIVE if i == index else _TAB_BUTTON_BASE for i in range(4))
    return styles, classes


def _tournament_subnav(slug, tab_classes):
    """Page-level subnav: Info/Start Sheet/Leaderboard/Live Round are
    client-side tabs (all four panel groups are always in the DOM, toggled
    by switch_tournament_tab below), Return to Club is a real navigation
    link -- same always-render-every-panel-toggle-with-style approach the
    entry button uses, so the tab buttons' ids are stable across renders.
    tab_classes (from _tab_visibility) is what makes the *initial* active
    tab match whatever ?tab= value got here, rather than always opening on
    Tournament Info."""
    info_class, startsheet_class, leaderboard_class, liveround_class = tab_classes
    return html.Div(
        className="t3g-tournament-subnav",
        children=html.Div(
            className="t3g-tournament-subnav-inner",
            children=[
                html.Div(
                    className="t3g-tournament-tabs",
                    children=[
                        html.Button(
                            "Tournament Info",
                            id="tournament-tab-info-button",
                            className=info_class,
                            n_clicks=0,
                        ),
                        html.Button(
                            "Start Sheet",
                            id="tournament-tab-startsheet-button",
                            className=startsheet_class,
                            n_clicks=0,
                        ),
                        html.Button(
                            "Leaderboard",
                            id="tournament-tab-leaderboard-button",
                            className=leaderboard_class,
                            n_clicks=0,
                        ),
                        html.Button(
                            "Live Round",
                            id="tournament-tab-liveround-button",
                            className=liveround_class,
                            n_clicks=0,
                        ),
                    ],
                ),
                dcc.Link(
                    "Return to Club",
                    href=f"/clubs/{slug}",
                    className="t3g-tournament-subnav-back",
                ),
            ],
        ),
    )


def _add_player_modal(options):
    return dbc.Modal(
        id="tournament-add-player-modal",
        is_open=False,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("Add Players")),
            dbc.ModalBody(
                children=[
                    dcc.Dropdown(
                        id="tournament-add-player-input",
                        options=options,
                        placeholder="Select players from the club",
                        multi=True,
                    ),
                    html.Div(id="tournament-add-player-error", className="text-danger mt-2"),
                ],
            ),
            dbc.ModalFooter(
                [
                    dbc.Button("Cancel", id="tournament-add-player-cancel", color="secondary"),
                    dbc.Button("Add Players", id="tournament-add-player-submit", color="primary"),
                ]
            ),
        ],
    )


def _live_round_panel(tournament, player_id):
    """Live Round tab -- purely a read-only reflection of each round's
    state now, scoped to just the viewing player's own tee time grouping
    (not a full-field overview of every grouping -- that's a separate,
    bigger thing this doesn't try to be). Starting a round is a Start
    Sheet action (see _tee_times_panel's _my_group_action) since that's
    where the tee times/groupings themselves live; this tab just shows,
    per round: the *actual scorecard embedded right here* once it's in
    progress (started by them or a groupmate -- everyone in the slot was
    made an equal accepted participant the moment it was started, see
    start_tournament_round) rather than just a link to go click through
    to, a Finished badge once it's done, a plain "not started yet" hint
    pointing back at Start Sheet, or a note if they're not in a grouping
    for that round at all."""
    rounds = tournament.get("rounds", [])
    round_sections = []

    for r in rounds:
        course_label = r.get("club_name") or "Course TBC"
        if r.get("course_name"):
            course_label += f", {r['course_name']}"
        round_heading = f"Round {r['round_number']} — {r.get('round_date', '')} ({course_label})"

        groups = r.get("tee_times", [])
        my_group = next(
            (g for g in groups if any(p["player_id"] == player_id for p in g.get("players", []))),
            None,
        )

        # extra_children is how the embedded scorecard's own stores/modals
        # (from render_live_round_body) get spliced straight into this
        # round's section.
        extra_children = []

        if my_group is None:
            body = html.P(
                "You're not in a tee time grouping for this round yet.",
                className="t3g-empty-state mb-0",
            )
        else:
            live_round = my_group.get("live_round")

            if live_round is None:
                body = html.P(
                    "Live round hasn't started yet -- start it from the Start Sheet tab.",
                    className="t3g-empty-state mb-0",
                )
            elif live_round.get("status") == "in_progress":
                # The actual scorecard, not a link to it -- same markup
                # (and same fixed component ids) the standalone /play
                # page's scorecard mode renders, safe to reuse here because
                # a player can
                # only ever have one round (casual or tournament) actually
                # live at a time, so this only ever appears in one place
                # in the DOM at once. See render_live_round_body's
                # docstring in components/live_scorecard.py.
                round_resp = requests.get(
                    f"{API_BASE_URL}/rounds/{live_round['id']}", params={"viewer_player_id": player_id}
                )
                if round_resp.status_code == 200:
                    body = None
                    extra_children = render_live_round_body(round_resp.json(), player_id)
                else:
                    body = html.P(
                        "Couldn't load the live round right now.", className="t3g-empty-state mb-0"
                    )
            else:
                body = html.Span("Round completed", className="t3g-liveround-finished-badge")

        round_sections.append(
            html.Div(
                className="t3g-modal-section t3g-teetime-round-section",
                children=[
                    html.Div(round_heading, className="t3g-modal-label t3g-tournament-rounds-label"),
                    body,
                    *extra_children,
                ],
            )
        )

    if not round_sections:
        round_sections = [html.P("No rounds set up yet.", className="t3g-empty-state")]

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Live Round"),
            html.Div(round_sections, className="t3g-panel-body"),
        ],
    )


def layout(slug=None, tournament_id=None, tab=None, **kwargs):
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

    # This is exactly where a "clubs" category notification's own url
    # points (tee times published -- see backend/services/
    # tournament_tee_times.py's create_notification call) -- most people
    # will land here straight from that notification, bypassing the
    # Clubs index entirely, so this needs its own copy of clubs.py's
    # mark-read call rather than relying on that one alone.
    try:
        requests.post(f"{API_BASE_URL}/notifications/{player_id}/read/clubs")
    except requests.RequestException:
        pass

    is_admin = bool(player_id) and str(club.get("club_admin")) == player_id
    entrants = tournament.get("entrants", [])
    my_entry = next((e for e in entrants if str(e.get("player_id")) == player_id), None)

    add_options = []
    if is_admin:
        entered_ids = {e["player_id"] for e in entrants if e["status"] in _ENTERED_STATUSES}
        roster_resp = requests.get(f"{API_BASE_URL}/club-players/club/{club['id']}")
        roster = roster_resp.json() if roster_resp.status_code == 200 else []
        add_options = [
            {"label": _roster_player_label(row.get("players")), "value": row["player_id"]}
            for row in roster
            if row["player_id"] not in entered_ids
        ]

    (info_style, startsheet_style, leaderboard_style, liveround_style), tab_classes = _tab_visibility(tab)

    return html.Div(
        className="t3g-page t3g-club-page",
        children=[
            dcc.Store(id="tournament-id-store", data=tournament_id),
            _tournament_subnav(slug, tab_classes),
            html.Div(
                id="tournament-tab-panel-info",
                style=info_style,
                children=[
                    html.Div(
                        className="t3g-panel-grid",
                        children=[
                            _tournament_info_panel(tournament, is_admin),
                            _tournament_info_leaderboard_panel(tournament),
                        ],
                    ),
                    _entrants_panel(tournament, entrants, my_entry, is_admin),
                ],
            ),
            html.Div(
                id="tournament-tab-panel-startsheet",
                style=startsheet_style,
                children=_tee_times_panel(tournament, is_admin, player_id),
            ),
            html.Div(
                id="tournament-tab-panel-leaderboard",
                style=leaderboard_style,
                children=_leaderboard_panel(tournament),
            ),
            html.Div(
                id="tournament-tab-panel-liveround",
                style=liveround_style,
                children=_live_round_panel(tournament, player_id),
            ),
            dcc.Store(id="tournament-entry-action-store", data=_entry_toggle_meta(tournament, my_entry)[1]),
            _add_player_modal(add_options),
            dcc.Store(id="tournament-edit-original-store", data=tournament),
            _tournament_edit_modal(tournament),
            dcc.Location(id="tournament-entry-redirect", refresh=True),
            dcc.Location(id="tournament-admin-action-redirect", refresh=True),
            dcc.Location(id="tournament-add-player-redirect", refresh=True),
            dcc.Location(id="tournament-remove-entrant-redirect", refresh=True),
            dcc.Location(id="tournament-edit-redirect", refresh=True),
        ],
    )


@callback(
    Output("tournament-tab-panel-info", "style"),
    Output("tournament-tab-panel-startsheet", "style"),
    Output("tournament-tab-panel-leaderboard", "style"),
    Output("tournament-tab-panel-liveround", "style"),
    Output("tournament-tab-info-button", "className"),
    Output("tournament-tab-startsheet-button", "className"),
    Output("tournament-tab-leaderboard-button", "className"),
    Output("tournament-tab-liveround-button", "className"),
    Input("tournament-tab-info-button", "n_clicks"),
    Input("tournament-tab-startsheet-button", "n_clicks"),
    Input("tournament-tab-leaderboard-button", "n_clicks"),
    Input("tournament-tab-liveround-button", "n_clicks"),
    prevent_initial_call=True,
)
def switch_tournament_tab(info_clicks, startsheet_clicks, leaderboard_clicks, liveround_clicks):
    # Four tabs now -- same always-in-the-DOM, toggle-by-style approach,
    # just picking which one panel gets shown (and which one button gets
    # the active class) based on whichever tab was actually clicked, with
    # everything else hidden/inactive.
    hidden = {"display": "none"}
    shown = {}
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "tournament-tab-startsheet-button":
        return hidden, shown, hidden, hidden, _TAB_BUTTON_BASE, _TAB_BUTTON_ACTIVE, _TAB_BUTTON_BASE, _TAB_BUTTON_BASE
    if triggered_id == "tournament-tab-leaderboard-button":
        return hidden, hidden, shown, hidden, _TAB_BUTTON_BASE, _TAB_BUTTON_BASE, _TAB_BUTTON_ACTIVE, _TAB_BUTTON_BASE
    if triggered_id == "tournament-tab-liveround-button":
        return hidden, hidden, hidden, shown, _TAB_BUTTON_BASE, _TAB_BUTTON_BASE, _TAB_BUTTON_BASE, _TAB_BUTTON_ACTIVE
    return shown, hidden, hidden, hidden, _TAB_BUTTON_ACTIVE, _TAB_BUTTON_BASE, _TAB_BUTTON_BASE, _TAB_BUTTON_BASE


@callback(
    Output("tournament-entry-error", "children"),
    Output("tournament-entry-redirect", "href"),
    Input("tournament-entry-toggle-button", "n_clicks"),
    State("tournament-entry-action-store", "data"),
    State("tournament-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_tournament_entry(n_clicks, action, tournament_id, current_pathname):
    player_id = session.get("player_id")

    if action == "withdraw":
        response = requests.delete(f"{API_BASE_URL}/tournaments/{tournament_id}/entrants/{player_id}")
        if response.status_code == 200:
            return "", f"{current_pathname}?_r={time.time()}"
        return "Couldn't withdraw. Try again.", dash.no_update

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


@callback(
    Output("tournament-admin-action-error", "children"),
    Output("tournament-admin-action-redirect", "href"),
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


@callback(
    Output("tournament-add-player-modal", "is_open"),
    Output("tournament-add-player-error", "children"),
    Output("tournament-add-player-redirect", "href"),
    Output("tournament-add-player-input", "value"),
    Input("tournament-add-player-open-button", "n_clicks"),
    Input("tournament-add-player-cancel", "n_clicks"),
    Input("tournament-add-player-submit", "n_clicks"),
    State("tournament-add-player-input", "value"),
    State("tournament-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_add_player_modal(open_clicks, cancel_clicks, submit_clicks, selected_player_ids, tournament_id, current_pathname):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "tournament-add-player-open-button":
        return True, "", dash.no_update, None

    if triggered_id == "tournament-add-player-cancel":
        return False, "", dash.no_update, None

    if triggered_id == "tournament-add-player-submit":
        if not selected_player_ids:
            return True, "Select at least one player first.", dash.no_update, dash.no_update

        admin_id = session.get("player_id")
        failed_labels = []
        for player_id in selected_player_ids:
            response = requests.post(
                f"{API_BASE_URL}/tournaments/{tournament_id}/entrants/{player_id}/add",
                params={"admin_id": admin_id},
            )
            if response.status_code not in (200, 201):
                try:
                    detail = response.json().get("detail", "Couldn't add that player.")
                    if not isinstance(detail, str):
                        detail = "Couldn't add that player."
                except ValueError:
                    detail = "Couldn't add that player."
                failed_labels.append(detail)

        if failed_labels:
            # Some added, some didn't -- still refresh so the successful
            # ones show up, but surface the failures rather than silently
            # dropping them.
            return True, "; ".join(failed_labels), f"{current_pathname}?_r={time.time()}", dash.no_update

        return False, "", f"{current_pathname}?_r={time.time()}", None

    return dash.no_update, dash.no_update, dash.no_update, dash.no_update


@callback(
    Output("tournament-remove-entrant-error", "children"),
    Output("tournament-remove-entrant-redirect", "href"),
    Input({"type": "tournament-entrant-remove", "player_id": ALL}, "n_clicks"),
    State("tournament-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_remove_entrant(remove_clicks, tournament_id, current_pathname):
    triggered_id = dash.ctx.triggered_id
    # Same phantom-trigger guard as handle_entrant_response -- a fresh
    # Remove button appearing after another row is removed can re-fire
    # this callback with no real click behind it.
    if not triggered_id or not any(remove_clicks or []):
        return dash.no_update, dash.no_update

    admin_id = session.get("player_id")
    target_player_id = triggered_id["player_id"]

    response = requests.delete(
        f"{API_BASE_URL}/tournaments/{tournament_id}/entrants/{target_player_id}/admin",
        params={"admin_id": admin_id},
    )
    if response.status_code == 200:
        return "", f"{current_pathname}?_r={time.time()}"

    try:
        detail = response.json().get("detail", "Couldn't remove that player.")
        if not isinstance(detail, str):
            detail = "Couldn't remove that player."
    except ValueError:
        detail = "Couldn't remove that player."
    return detail, dash.no_update


@callback(
    Output({"type": "tournament-teetime-error", "round_id": MATCH}, "children"),
    Output({"type": "tournament-teetime-redirect", "round_id": MATCH}, "href"),
    Input({"type": "tournament-teetime-generate", "round_id": MATCH}, "n_clicks"),
    State({"type": "tournament-teetime-first-time", "round_id": MATCH}, "value"),
    State("tournament-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_generate_tee_times(n_clicks, first_tee_time, tournament_id, current_pathname):
    # MATCH (not ALL) here -- each round's Generate button only ever
    # touches its own round, so there's no need for the ALL-based
    # phantom-trigger guard the entrant approve/reject/remove callbacks
    # use; a plain "was this actually clicked" check covers it.
    if not n_clicks:
        return dash.no_update, dash.no_update

    if not first_tee_time:
        return "Enter a first tee time.", dash.no_update

    round_id = dash.ctx.triggered_id["round_id"]
    admin_id = session.get("player_id")
    response = requests.post(
        f"{API_BASE_URL}/tournaments/{tournament_id}/rounds/{round_id}/tee-times/generate",
        json={"admin_id": admin_id, "first_tee_time": first_tee_time},
    )
    if response.status_code == 200:
        return "", f"{current_pathname}?_r={time.time()}"

    try:
        detail = response.json().get("detail", "Couldn't generate tee times.")
        if not isinstance(detail, str):
            detail = "Couldn't generate tee times."
    except ValueError:
        detail = "Couldn't generate tee times."
    return detail, dash.no_update


@callback(
    Output({"type": "tournament-teetime-assign", "round_id": MATCH, "tee_time_id": ALL, "slot": ALL}, "options"),
    Input({"type": "tournament-teetime-assign", "round_id": MATCH, "tee_time_id": ALL, "slot": ALL}, "value"),
    State({"type": "tournament-teetime-assign", "round_id": MATCH, "tee_time_id": ALL, "slot": ALL}, "id"),
    State({"type": "tournament-teetime-entrant-options", "round_id": MATCH}, "data"),
)
def filter_tee_time_assign_options(values, ids, entrant_options):
    # Prevents picking the same player into more than one slot in the first
    # place, rather than only catching it after the fact at Save (that check
    # in handle_save_tee_time_assignments stays too, as a backstop). Every
    # dropdown in the round shares this one callback (round_id is MATCH,
    # tee_time_id/slot are ALL) -- whenever any of them changes, every
    # dropdown's options get recomputed as "the round's full entrant list
    # minus whoever's currently selected in one of the *other* dropdowns."
    # A dropdown's own current selection is deliberately left out of its own
    # exclusion set, otherwise picking someone would immediately make them
    # disappear from the very dropdown they were just picked into. No
    # prevent_initial_call -- this should run on first load too, in case
    # stale/duplicate data ever ended up saved from before this existed.
    entrant_options = entrant_options or []
    filtered = []
    for index in range(len(ids)):
        other_selected = {v for i, v in enumerate(values) if i != index and v}
        filtered.append([opt for opt in entrant_options if opt["value"] not in other_selected])
    return filtered


@callback(
    Output({"type": "tournament-teetime-error", "round_id": MATCH}, "children", allow_duplicate=True),
    Output({"type": "tournament-teetime-redirect", "round_id": MATCH}, "href", allow_duplicate=True),
    Input({"type": "tournament-teetime-save-assignments", "round_id": MATCH}, "n_clicks"),
    State({"type": "tournament-teetime-assign", "round_id": MATCH, "tee_time_id": ALL, "slot": ALL}, "value"),
    State({"type": "tournament-teetime-assign", "round_id": MATCH, "tee_time_id": ALL, "slot": ALL}, "id"),
    State("tournament-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_save_tee_time_assignments(n_clicks, values, ids, tournament_id, current_pathname):
    # Same MATCH-on-round_id / ALL-on-the-rest shape as club.py's/this
    # file's other per-round MATCH callbacks -- see handle_generate_tee_
    # times's comment on why both Outputs need the same MATCH key. Reading
    # the "id" prop alongside "value" is what ties each dropdown back to
    # its tee_time_id (the "slot"/column position is purely a display
    # detail -- what actually gets saved is just "this player is in this
    # group", not which column they sat in).
    if not n_clicks:
        return dash.no_update, dash.no_update

    round_id = dash.ctx.triggered_id["round_id"]

    selected_player_ids = [v for v in values if v]
    if len(selected_player_ids) != len(set(selected_player_ids)):
        return "The same player is selected in more than one slot.", dash.no_update

    assignments = {value: id_dict["tee_time_id"] for id_dict, value in zip(ids, values) if value}

    admin_id = session.get("player_id")
    response = requests.patch(
        f"{API_BASE_URL}/tournaments/{tournament_id}/rounds/{round_id}/tee-times/assignments",
        json={"admin_id": admin_id, "assignments": assignments},
    )
    if response.status_code == 200:
        return "", f"{current_pathname}?_r={time.time()}"

    try:
        detail = response.json().get("detail", "Couldn't save those assignments.")
        if not isinstance(detail, str):
            detail = "Couldn't save those assignments."
    except ValueError:
        detail = "Couldn't save those assignments."
    return detail, dash.no_update


@callback(
    Output({"type": "tournament-teetime-error", "round_id": MATCH}, "children", allow_duplicate=True),
    Output({"type": "tournament-teetime-redirect", "round_id": MATCH}, "href", allow_duplicate=True),
    Input({"type": "tournament-teetime-update-save", "round_id": MATCH, "tee_time_id": ALL}, "n_clicks"),
    State({"type": "tournament-teetime-update-input", "round_id": MATCH, "tee_time_id": ALL}, "value"),
    State({"type": "tournament-teetime-update-input", "round_id": MATCH, "tee_time_id": ALL}, "id"),
    State("tournament-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_update_tee_time(save_clicks, values, ids, tournament_id, current_pathname):
    # Same ALL-with-phantom-trigger-guard shape as handle_entrant_response/
    # handle_remove_entrant -- there's one Save button per tee time slot in
    # the round, all sharing this one callback (round_id is the MATCH key,
    # tee_time_id is ALL), so a fresh Save button appearing elsewhere on the
    # page (e.g. after Generate rebuilds the whole slot list) can re-fire
    # this with no real click behind it.
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(save_clicks or []):
        return dash.no_update, dash.no_update

    target_tee_time_id = triggered_id["tee_time_id"]
    round_id = triggered_id["round_id"]

    new_time = None
    for id_dict, value in zip(ids, values):
        if id_dict["tee_time_id"] == target_tee_time_id:
            new_time = value
            break

    if not new_time:
        return "Enter a tee time first.", dash.no_update

    admin_id = session.get("player_id")
    response = requests.patch(
        f"{API_BASE_URL}/tournaments/{tournament_id}/rounds/{round_id}/tee-times/{target_tee_time_id}",
        json={"admin_id": admin_id, "tee_time": new_time},
    )
    if response.status_code == 200:
        return "", f"{current_pathname}?_r={time.time()}"

    try:
        detail = response.json().get("detail", "Couldn't update that tee time.")
        if not isinstance(detail, str):
            detail = "Couldn't update that tee time."
    except ValueError:
        detail = "Couldn't update that tee time."
    return detail, dash.no_update


@callback(
    Output({"type": "tournament-liveround-error", "round_id": MATCH}, "children"),
    Output({"type": "tournament-liveround-redirect", "round_id": MATCH}, "href"),
    Input({"type": "tournament-start-live-round", "round_id": MATCH, "tee_time_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def handle_start_live_round(start_clicks):
    # MATCH on round_id, ALL on tee_time_id -- there's only ever one Start
    # button per round (the viewer's own grouping), but the id still
    # carries tee_time_id so the handler knows which slot to start without
    # a separate State lookup, and every key in a pattern-matched id has
    # to be either a literal or a wildcard, so ALL it is even though it
    # only ever matches one component. Same phantom-trigger guard as
    # handle_remove_entrant/handle_entrant_response.
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(start_clicks or []):
        return dash.no_update, dash.no_update

    tee_time_id = triggered_id["tee_time_id"]
    player_id = session.get("player_id")

    response = requests.post(
        f"{API_BASE_URL}/rounds/tournament/{tee_time_id}",
        json={"player_id": player_id},
    )
    if response.status_code == 200:
        round_data = response.json()
        return "", f"/play?round_id={round_data['id']}"

    try:
        detail = response.json().get("detail", "Couldn't start the live round.")
        if not isinstance(detail, str):
            detail = "Couldn't start the live round."
    except ValueError:
        detail = "Couldn't start the live round."
    return detail, dash.no_update


@callback(
    Output("tournament-leaderboard-store", "data"),
    Output("tournament-leaderboard-error", "children"),
    Input("tournament-leaderboard-round-store", "data"),
    Input("tournament-leaderboard-refresh-interval", "n_intervals"),
    State("tournament-leaderboard-rounds-store", "data"),
    State("tournament-id-store", "data"),
)
def load_tournament_leaderboard(round_key, n_intervals, rounds, tournament_id):
    # No prevent_initial_call -- this has to run on first render too (the
    # round tabs' own default value, "overall", is what picks the initial
    # round), not just on later round-tab clicks. The Interval Input is
    # what makes this "live": it re-fires this same fetch every
    # _LEADERBOARD_REFRESH_INTERVAL_MS regardless of whether the round
    # selection changed, so scores update on their own while someone's
    # sitting on this tab watching a round in progress.
    if not tournament_id:
        raise PreventUpdate

    # "Overall" isn't a real round id -- resolve it to whichever round is
    # currently the tournament's leading edge (same logic the round
    # selector's default used to bake in silently), against the rounds
    # snapshot taken when this panel was built. A specific "Round N" tab
    # instead just passes its own real round_id straight through.
    round_id = round_key
    if round_key == "overall":
        resolved_round = _default_leaderboard_round({"rounds": rounds or []})
        round_id = resolved_round["id"] if resolved_round else None

    if not round_id:
        raise PreventUpdate

    response = requests.get(
        f"{API_BASE_URL}/tournaments/{tournament_id}/leaderboard", params={"round_id": round_id}
    )
    if response.status_code != 200:
        return dash.no_update, "Couldn't load the leaderboard right now."
    return response.json(), ""


@callback(
    Output("tournament-leaderboard-table-container", "children"),
    Input("tournament-leaderboard-store", "data"),
    Input("tournament-leaderboard-format-store", "data"),
    Input("tournament-leaderboard-view-store", "data"),
)
def render_tournament_leaderboard_table(leaderboard_data, mode, view):
    # Fires on every leaderboard fetch (round change or the refresh
    # interval), every format-toggle click, and every view-toggle click --
    # none of the three ever need a new request, since the backend already
    # computed all three (gross/stableford/nett) lines up front and both
    # views are just different ways of rendering that same fetched data.
    if not leaderboard_data:
        raise PreventUpdate
    mode = mode or "gross"
    if view == "simple":
        return _leaderboard_simple_table(leaderboard_data, mode)
    return _leaderboard_table(leaderboard_data, mode)


@callback(
    Output("tournament-leaderboard-format-store", "data"),
    Output({"type": "tournament-leaderboard-format-button", "mode": ALL}, "className"),
    Input({"type": "tournament-leaderboard-format-button", "mode": ALL}, "n_clicks"),
    State({"type": "tournament-leaderboard-format-button", "mode": ALL}, "id"),
    prevent_initial_call=True,
)
def switch_tournament_leaderboard_format(clicks, ids):
    # Same phantom-trigger guard as the other ALL-pattern button rows on
    # this page (e.g. handle_entrant_response) -- all 3 format buttons
    # share this one callback.
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(clicks or []):
        raise PreventUpdate

    active_mode = triggered_id["mode"]
    classes = _leaderboard_format_classes(active_mode)
    return active_mode, [classes[id_dict["mode"]] for id_dict in ids]


@callback(
    Output("tournament-leaderboard-view-store", "data"),
    Output({"type": "tournament-leaderboard-view-button", "view": ALL}, "className"),
    Input({"type": "tournament-leaderboard-view-button", "view": ALL}, "n_clicks"),
    State({"type": "tournament-leaderboard-view-button", "view": ALL}, "id"),
    prevent_initial_call=True,
)
def switch_tournament_leaderboard_view(clicks, ids):
    # Same pattern as switch_tournament_leaderboard_format just above --
    # both Detailed/Simple buttons share this one callback.
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(clicks or []):
        raise PreventUpdate

    active_view = triggered_id["view"]
    classes = _leaderboard_view_classes(active_view)
    return active_view, [classes[id_dict["view"]] for id_dict in ids]


@callback(
    Output("tournament-leaderboard-round-store", "data"),
    Output({"type": "tournament-leaderboard-round-button", "round_id": ALL}, "className"),
    Input({"type": "tournament-leaderboard-round-button", "round_id": ALL}, "n_clicks"),
    State({"type": "tournament-leaderboard-round-button", "round_id": ALL}, "id"),
    prevent_initial_call=True,
)
def switch_tournament_leaderboard_round(clicks, ids):
    # Same pattern again -- "Overall" plus one button per round all share
    # this one callback; round_key here is either "overall" or a real
    # round_id, load_tournament_leaderboard is what tells the two apart.
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(clicks or []):
        raise PreventUpdate

    active_round = triggered_id["round_id"]
    round_keys = [id_dict["round_id"] for id_dict in ids]
    classes = _leaderboard_round_classes(round_keys, active_round)
    return active_round, [classes[key] for key in round_keys]


@callback(
    Output("tournament-info-leaderboard-store", "data"),
    Output("tournament-info-leaderboard-error", "children"),
    Input("tournament-info-leaderboard-refresh-interval", "n_intervals"),
    State("tournament-info-leaderboard-round-store", "data"),
    State("tournament-id-store", "data"),
)
def load_tournament_info_leaderboard(n_intervals, round_id, tournament_id):
    # No prevent_initial_call -- has to run on first render too, same
    # reasoning as load_tournament_leaderboard above. Always the one round
    # _tournament_info_leaderboard_panel resolved at build time (see
    # tournament-info-leaderboard-round-store) -- this panel has no round
    # picker, so there's nothing to react to there, just the interval
    # keeping that one round's numbers current.
    if not round_id or not tournament_id:
        raise PreventUpdate

    response = requests.get(
        f"{API_BASE_URL}/tournaments/{tournament_id}/leaderboard", params={"round_id": round_id}
    )
    if response.status_code != 200:
        return dash.no_update, "Couldn't load the leaderboard right now."
    return response.json(), ""


@callback(
    Output("tournament-info-leaderboard-table-container", "children"),
    Input("tournament-info-leaderboard-store", "data"),
    Input("tournament-info-leaderboard-format-store", "data"),
)
def render_tournament_info_leaderboard_table(leaderboard_data, mode):
    if not leaderboard_data:
        raise PreventUpdate
    return _leaderboard_simple_table(leaderboard_data, mode or "gross", clickable=False)


@callback(
    Output("tournament-info-leaderboard-format-store", "data"),
    Output({"type": "tournament-info-leaderboard-format-button", "mode": ALL}, "className"),
    Input({"type": "tournament-info-leaderboard-format-button", "mode": ALL}, "n_clicks"),
    State({"type": "tournament-info-leaderboard-format-button", "mode": ALL}, "id"),
    prevent_initial_call=True,
)
def switch_tournament_info_leaderboard_format(clicks, ids):
    # Same phantom-trigger-guard pattern as switch_tournament_leaderboard_
    # format -- a separate callback (not the same one) because it's a
    # distinct button id type (tournament-info-leaderboard-format-button
    # vs tournament-leaderboard-format-button) and a distinct store, so
    # switching format in one panel never touches the other.
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(clicks or []):
        raise PreventUpdate

    active_mode = triggered_id["mode"]
    classes = _leaderboard_format_classes(active_mode)
    return active_mode, [classes[id_dict["mode"]] for id_dict in ids]


@callback(
    Output("tournament-leaderboard-scorecard-modal", "is_open"),
    Output("tournament-leaderboard-scorecard-modal-title", "children"),
    Output("tournament-leaderboard-scorecard-modal-body", "children"),
    Input({"type": "tournament-leaderboard-player-row", "player_id": ALL}, "n_clicks"),
    Input("tournament-leaderboard-scorecard-close", "n_clicks"),
    State("tournament-leaderboard-store", "data"),
    prevent_initial_call=True,
)
def toggle_tournament_leaderboard_scorecard(row_clicks, close_clicks, leaderboard_data):
    # Every player row shares this one callback (player_id is ALL, there's
    # no per-row callback) -- same phantom-trigger guard as the other
    # ALL-pattern rows on this page, since the table gets rebuilt (fresh
    # n_clicks=0 rows) on every leaderboard refresh, round change, and
    # format switch.
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "tournament-leaderboard-scorecard-close":
        return False, dash.no_update, dash.no_update

    if not triggered_id or not any(row_clicks or []):
        raise PreventUpdate

    player_id = triggered_id["player_id"]
    leaderboard_data = leaderboard_data or {}
    player = next((p for p in leaderboard_data.get("players", []) if p["player_id"] == player_id), None)
    if not player:
        raise PreventUpdate

    title = f"{player['name']} — Round {leaderboard_data.get('round_number')}"
    if player.get("is_nr"):
        title += " (No Return)"
    body = _leaderboard_player_scorecard(player, leaderboard_data.get("holes", []))
    return True, title, body


@callback(
    Output("tournament-edit-rounds-container", "children", allow_duplicate=True),
    Input("tournament-edit-add-round", "n_clicks"),
    Input({"type": "tournament-edit-round-remove", "index": ALL}, "n_clicks"),
    State("tournament-edit-rounds-container", "children"),
    prevent_initial_call=True,
)
def edit_tournament_edit_rounds(add_clicks, remove_clicks_list, current_rows):
    # Same add/remove logic and phantom-trigger guard as club.py's
    # edit_tournament_rounds -- see that function's comments.
    triggered_id = dash.ctx.triggered_id
    current_rows = current_rows or []

    if triggered_id == "tournament-edit-add-round":
        next_index = max((row["props"]["id"]["index"] for row in current_rows), default=-1) + 1
        return current_rows + [_edit_round_row(next_index)]

    if (
        isinstance(triggered_id, dict)
        and triggered_id.get("type") == "tournament-edit-round-remove"
        and any(remove_clicks_list or [])
    ):
        if len(current_rows) <= 1:
            return dash.no_update
        removed_index = triggered_id["index"]
        return [row for row in current_rows if row["props"]["id"]["index"] != removed_index]

    return dash.no_update


@callback(
    Output({"type": "tournament-edit-round-course", "index": MATCH}, "options"),
    Input({"type": "tournament-edit-round-course", "index": MATCH}, "search_value"),
    Input({"type": "tournament-edit-round-course", "index": MATCH}, "value"),
    State({"type": "tournament-edit-round-course", "index": MATCH}, "options"),
    prevent_initial_call=True,
)
def search_tournament_edit_round_course_options(search_value, selected_course_id, current_options):
    # Same fix, same reasoning, as club.py's search_tournament_round_course_
    # options / home.py's search_course_options -- see either docstring.
    selected_option = next(
        (opt for opt in (current_options or []) if opt["value"] == selected_course_id),
        None,
    )

    if not search_value or len(search_value) < 2:
        return [selected_option] if selected_option else []

    response = requests.get(f"{API_BASE_URL}/courses/", params={"search": search_value})
    courses = response.json() if response.status_code == 200 else []
    options = [{"label": _course_label(c), "value": c["id"]} for c in courses]

    if selected_option and not any(opt["value"] == selected_option["value"] for opt in options):
        options.append(selected_option)

    return options


@callback(
    Output({"type": "tournament-edit-round-tee", "index": MATCH}, "options"),
    Output({"type": "tournament-edit-round-tee", "index": MATCH}, "disabled"),
    Output({"type": "tournament-edit-round-tee", "index": MATCH}, "value"),
    Input({"type": "tournament-edit-round-course", "index": MATCH}, "value"),
    prevent_initial_call=True,
)
def load_tournament_edit_round_tees(course_id):
    # prevent_initial_call means a prefilled row (course already selected
    # when the modal's rows were built) never re-fires this on load -- its
    # tee value/options came from _edit_round_row's own prefill instead.
    # This only fires once someone actually changes a row's course.
    if not course_id:
        return [], True, None

    response = requests.post(f"{API_BASE_URL}/courses/{course_id}/scorecard")
    if response.status_code != 200:
        return [], True, None

    tees = response.json().get("tees", [])
    if not tees:
        return [], True, None

    tee_options = [
        {
            "label": f"{tee['name']} tees" + (f" (Par {tee['par']})" if tee.get("par") else ""),
            "value": tee["id"],
        }
        for tee in tees
    ]
    return tee_options, False, None


@callback(
    Output("tournament-edit-min-handicap-store", "data"),
    Output("tournament-edit-min-handicap-display", "children"),
    Input("tournament-edit-min-handicap-plus", "n_clicks"),
    Input("tournament-edit-min-handicap-minus", "n_clicks"),
    State("tournament-edit-min-handicap-store", "data"),
    prevent_initial_call=True,
)
def adjust_tournament_edit_min_handicap(plus_clicks, minus_clicks, current):
    return _adjust_handicap_stepper(
        dash.ctx.triggered_id, "tournament-edit-min-handicap-plus", "tournament-edit-min-handicap-minus", current
    )


@callback(
    Output("tournament-edit-max-handicap-store", "data"),
    Output("tournament-edit-max-handicap-display", "children"),
    Input("tournament-edit-max-handicap-plus", "n_clicks"),
    Input("tournament-edit-max-handicap-minus", "n_clicks"),
    State("tournament-edit-max-handicap-store", "data"),
    prevent_initial_call=True,
)
def adjust_tournament_edit_max_handicap(plus_clicks, minus_clicks, current):
    return _adjust_handicap_stepper(
        dash.ctx.triggered_id, "tournament-edit-max-handicap-plus", "tournament-edit-max-handicap-minus", current
    )


@callback(
    Output("tournament-edit-modal", "is_open"),
    Output("tournament-edit-error", "children"),
    Output("tournament-edit-redirect", "href"),
    Output("tournament-edit-rounds-container", "children"),
    Output("tournament-edit-name-input", "value"),
    Output("tournament-edit-format-input", "value"),
    Output("tournament-edit-entry-mode-input", "value"),
    Output("tournament-edit-grouping-method-input", "value"),
    Output("tournament-edit-min-handicap-store", "data", allow_duplicate=True),
    Output("tournament-edit-min-handicap-display", "children", allow_duplicate=True),
    Output("tournament-edit-max-handicap-store", "data", allow_duplicate=True),
    Output("tournament-edit-max-handicap-display", "children", allow_duplicate=True),
    Input("tournament-edit-button", "n_clicks"),
    Input("tournament-edit-cancel", "n_clicks"),
    Input("tournament-edit-submit", "n_clicks"),
    State("tournament-edit-name-input", "value"),
    State("tournament-edit-format-input", "value"),
    State("tournament-edit-entry-mode-input", "value"),
    State("tournament-edit-grouping-method-input", "value"),
    State("tournament-edit-min-handicap-store", "data"),
    State("tournament-edit-max-handicap-store", "data"),
    State({"type": "tournament-edit-round-date", "index": ALL}, "date"),
    State({"type": "tournament-edit-round-course", "index": ALL}, "value"),
    State({"type": "tournament-edit-round-tee", "index": ALL}, "value"),
    State({"type": "tournament-edit-round-group-size", "index": ALL}, "value"),
    State("tournament-id-store", "data"),
    State("_pages_location", "pathname"),
    State("tournament-edit-original-store", "data"),
    prevent_initial_call=True,
)
def handle_tournament_edit_modal(
    open_clicks, cancel_clicks, submit_clicks,
    name, format_value, entry_mode, grouping_method, min_handicap, max_handicap,
    round_dates, round_courses, round_tees, round_group_sizes,
    tournament_id, current_pathname, original_tournament,
):
    """Same shape as club.py's handle_tournament_modal, PATCHing instead of
    POSTing and pre-filling from tournament-edit-original-store instead of
    starting blank. Re-reads the original tournament on every open (rather
    than whatever's currently sitting in the fields) so cancelling a
    half-finished edit and reopening doesn't leave stale values behind --
    same "fresh modal every time it's opened" approach the create modal
    uses, just resetting to the saved tournament instead of to empty."""
    triggered_id = dash.ctx.triggered_id
    no_update_rest = (dash.no_update,) * 9

    if triggered_id == "tournament-edit-button":
        original_tournament = original_tournament or {}
        rounds = original_tournament.get("rounds", [])
        round_rows = [_edit_round_row(i, r) for i, r in enumerate(rounds)] or [_edit_round_row(0)]
        original_min = original_tournament.get("min_handicap")
        original_max = original_tournament.get("max_handicap")
        return (
            True, "", dash.no_update, round_rows,
            original_tournament.get("name"),
            original_tournament.get("format"),
            original_tournament.get("entry_mode", "self"),
            original_tournament.get("grouping_method", "random"),
            original_min, str(original_min) if original_min is not None else "–",
            original_max, str(original_max) if original_max is not None else "–",
        )

    if triggered_id == "tournament-edit-cancel":
        return (False, "", dash.no_update) + no_update_rest

    if triggered_id == "tournament-edit-submit":
        if not name or not name.strip():
            return (True, "Enter a tournament name.", dash.no_update) + no_update_rest
        if not format_value:
            return (True, "Choose a format.", dash.no_update) + no_update_rest
        if min_handicap is not None and max_handicap is not None and min_handicap > max_handicap:
            return (True, "Min handicap can't be greater than max.", dash.no_update) + no_update_rest

        rounds_payload = []
        for round_date, course_id, tee_id, group_size in zip(
            round_dates, round_courses, round_tees, round_group_sizes
        ):
            if not round_date or not course_id or not tee_id:
                return (
                    True, "Fill in the date, course, and tees for every round.", dash.no_update,
                ) + no_update_rest
            rounds_payload.append({
                "round_date": round_date,
                "course_id": course_id,
                "tee_id": tee_id,
                "group_size": group_size or _DEFAULT_GROUP_SIZE,
            })

        if not rounds_payload:
            return (True, "Add at least one round.", dash.no_update) + no_update_rest

        admin_id = session.get("player_id")
        response = requests.patch(
            f"{API_BASE_URL}/tournaments/{tournament_id}",
            json={
                "admin_id": admin_id,
                "name": name.strip(),
                "format": format_value,
                "entry_mode": entry_mode or "self",
                "grouping_method": grouping_method or "random",
                "min_handicap": min_handicap,
                "max_handicap": max_handicap,
                "rounds": rounds_payload,
            },
        )

        if response.status_code == 200:
            return (False, "", f"{current_pathname}?_r={time.time()}") + no_update_rest

        try:
            detail = response.json().get("detail", "Couldn't save those changes.")
            if not isinstance(detail, str):
                detail = "Couldn't save those changes."
        except ValueError:
            detail = "Couldn't save those changes."
        return (True, detail, dash.no_update) + no_update_rest

    return (dash.no_update, dash.no_update, dash.no_update) + no_update_rest