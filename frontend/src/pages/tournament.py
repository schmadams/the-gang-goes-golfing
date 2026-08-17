# target path: frontend/src/pages/tournament.py (full replacement)
import time

import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, MATCH, Input, Output, State, callback, dcc, html
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


def _tee_times_panel(tournament, is_admin):
    """Lives as its own panel card within the Tournament Info tab (next to
    Tournament Info and Entrants) rather than a separate subnav tab -- one
    section per round, each showing whatever groups have been generated
    plus, for admins, a first-tee-time input and a Generate button.
    Generating is always a full wholesale regenerate (see
    generate_tee_times's docstring) -- no confirmation, since re-running it
    after a withdrawal/late add is the expected, ordinary workflow, not a
    destructive edge case."""
    rounds = tournament.get("rounds", [])

    round_sections = []
    for r in rounds:
        round_id = r["id"]
        groups = r.get("tee_times", [])

        course_label = r.get("club_name") or "Course TBC"
        if r.get("course_name"):
            course_label += f", {r['course_name']}"
        round_heading = f"Round {r['round_number']} — {r.get('round_date', '')} ({course_label})"

        if groups:
            group_rows = [
                html.Div(
                    className="t3g-teetime-group",
                    children=[
                        html.Span(_format_tee_time(g.get("tee_time")), className="t3g-teetime-time"),
                        html.Span(
                            ", ".join(_entrant_label(p) for p in g.get("players", [])) or "No players",
                            className="t3g-teetime-players",
                        ),
                    ],
                )
                for g in groups
            ]
        else:
            group_rows = [html.P("No tee times generated yet.", className="t3g-empty-state mb-0")]

        admin_controls = None
        if is_admin:
            admin_controls = html.Div(
                className="t3g-teetime-generate-row",
                children=[
                    dcc.Input(
                        id={"type": "tournament-teetime-first-time", "round_id": round_id},
                        type="time",
                        value="08:00",
                        className="t3g-teetime-time-input",
                    ),
                    html.Button(
                        "Generate Tee Times",
                        id={"type": "tournament-teetime-generate", "round_id": round_id},
                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                        n_clicks=0,
                    ),
                ],
            )

        round_sections.append(
            html.Div(
                className="t3g-modal-section t3g-teetime-round-section",
                children=[
                    html.Div(round_heading, className="t3g-modal-label t3g-tournament-rounds-label"),
                    admin_controls,
                    html.Div(
                        id={"type": "tournament-teetime-error", "round_id": round_id},
                        className="text-danger mb-2",
                    ),
                    html.Div(group_rows, className="t3g-teetime-group-list"),
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


def _tournament_subnav(slug):
    """Page-level subnav: Info/Leaderboard are client-side tabs (both
    panel groups are always in the DOM, toggled by switch_tournament_tab
    below), Return to Club is a real navigation link -- same
    always-render-both-toggle-with-style approach the entry button uses,
    so the tab buttons' ids are stable across renders."""
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
                            className=_TAB_BUTTON_ACTIVE,
                            n_clicks=0,
                        ),
                        html.Button(
                            "Leaderboard",
                            id="tournament-tab-leaderboard-button",
                            className=_TAB_BUTTON_BASE,
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

    return html.Div(
        className="t3g-page t3g-club-page",
        children=[
            dcc.Store(id="tournament-id-store", data=tournament_id),
            _tournament_subnav(slug),
            html.Div(
                id="tournament-tab-panel-info",
                children=[
                    _tournament_info_panel(tournament, is_admin),
                    _entrants_panel(tournament, entrants, my_entry, is_admin),
                    _tee_times_panel(tournament, is_admin),
                ],
            ),
            html.Div(
                id="tournament-tab-panel-leaderboard",
                style={"display": "none"},
                children=_leaderboard_panel(entrants),
            ),
            dcc.Store(id="tournament-entry-action-store", data=_entry_toggle_meta(tournament, my_entry)[1]),
            _add_player_modal(add_options),
            dcc.Store(id="tournament-edit-original-store", data=tournament),
            _tournament_edit_modal(tournament),
            dcc.Location(id="tournament-entry-redirect", refresh=True),
            dcc.Location(id="tournament-admin-action-redirect", refresh=True),
            dcc.Location(id="tournament-add-player-redirect", refresh=True),
            dcc.Location(id="tournament-remove-entrant-redirect", refresh=True),
            dcc.Location(id="tournament-teetime-redirect", refresh=True),
            dcc.Location(id="tournament-edit-redirect", refresh=True),
        ],
    )


@callback(
    Output("tournament-tab-panel-info", "style"),
    Output("tournament-tab-panel-leaderboard", "style"),
    Output("tournament-tab-info-button", "className"),
    Output("tournament-tab-leaderboard-button", "className"),
    Input("tournament-tab-info-button", "n_clicks"),
    Input("tournament-tab-leaderboard-button", "n_clicks"),
    prevent_initial_call=True,
)
def switch_tournament_tab(info_clicks, leaderboard_clicks):
    if dash.ctx.triggered_id == "tournament-tab-leaderboard-button":
        return {"display": "none"}, {}, _TAB_BUTTON_BASE, _TAB_BUTTON_ACTIVE
    return {}, {"display": "none"}, _TAB_BUTTON_ACTIVE, _TAB_BUTTON_BASE


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
    Output("tournament-teetime-redirect", "href"),
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