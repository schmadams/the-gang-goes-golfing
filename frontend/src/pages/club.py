# target path: frontend/src/pages/club.py (replace entire file)
import time

import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, MATCH, Input, Output, State, callback, dcc, html
from flask import session

from components.scorecard import live_badge
from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path_template="/clubs/<slug>", name="Club")

_SORT_BUTTON_BASE = "t3g-panel-action-button t3g-panel-action-button--secondary"
_SORT_BUTTON_ACTIVE = "t3g-panel-action-button"

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
# Group size lives per round (a comp can run 3-balls one week, 4-balls the
# next), so it's a small dropdown on each round row rather than a
# tournament-wide setting like grouping method.
_GROUP_SIZE_OPTIONS = [{"label": f"{n} per group", "value": n} for n in range(2, 7)]
_DEFAULT_GROUP_SIZE = 4

# WHS caps Handicap Index at 54.0 (backend/services/whs.py's
# MAX_HANDICAP_INDEX) -- duplicated as a plain int here since the frontend
# talks to the backend over HTTP only and can't import its Python modules
# directly. -10 is just a practical floor for the min-handicap stepper
# (elite/plus handicaps go negative, but not meaningfully past this) --
# decrementing past it clears the field back to "unset" instead of
# continuing further negative.
_MAX_HANDICAP_INDEX = 54
_MIN_HANDICAP_FLOOR = -10


def _adjust_handicap_stepper(triggered_id, plus_id, minus_id, current):
    """Shared +/- logic for the min/max handicap steppers. Plus starts an
    unset field at 0 and climbs from there (capped at _MAX_HANDICAP_INDEX).
    Minus is NOT the mirror of that -- unlike live_round.py's shots/putts
    (where negative is meaningless, so minus from unset does nothing),
    handicaps go negative for scratch/plus players, so minus from unset
    should reach -1 on its own first press rather than needing a plus
    press first to "start" the field at 0. Once set, minus climbs down and
    clears back to "unset" once it hits _MIN_HANDICAP_FLOOR rather than
    continuing further negative."""
    value = current
    if triggered_id == plus_id:
        value = 0 if value is None else min(value + 1, _MAX_HANDICAP_INDEX)
    elif triggered_id == minus_id:
        value = -1 if value is None else (value - 1 if value > _MIN_HANDICAP_FLOOR else None)
    return value, str(value) if value is not None else "–"


def _player_label(row):
    return f"{row.get('first_name', '')} {row.get('surname', '')}".strip() or "Unknown player"


def _handicap_value(row):
    latest = row.get("latest_handicap")
    return latest["handicap"] if latest else None


def _format_handicap(row):
    value = _handicap_value(row)
    return f"{value}" if value is not None else "Not set"


def _sort_directory(directory, sort_by):
    if sort_by == "handicap":
        # Players with no handicap set sort to the bottom regardless of
        # direction, rather than landing at 0/first -- "Not set" isn't a
        # real handicap value and shouldn't look like the best one.
        return sorted(directory, key=lambda row: (_handicap_value(row) is None, _handicap_value(row) or 0))
    return sorted(directory, key=lambda row: _player_label(row).lower())


def _directory_table(directory, sort_by):
    ordered = _sort_directory(directory, sort_by)

    if not ordered:
        return html.P("No players in this club yet.", className="t3g-empty-state")

    rows = [
        html.Tr([html.Td(_player_label(row)), html.Td(_format_handicap(row))])
        for row in ordered
    ]

    return dbc.Table(
        [
            html.Thead(html.Tr([html.Th("Name"), html.Th("Handicap")])),
            html.Tbody(rows),
        ],
        className="t3g-club-directory-table",
        bordered=False,
        hover=True,
    )


def _admin_banner(is_admin):
    # Replaces the old bordered name/description panel's inline "ADMIN"
    # pill -- this is its own slim, full-width strip (styled after
    # layouts/subnav.py) so it reads as a status banner, not just a badge
    # buried in a box. Renders nothing at all for non-admins.
    if not is_admin:
        return None

    return html.Div(
        className="t3g-club-admin-banner",
        children=html.Div(
            "You're this club's admin",
            className="t3g-club-admin-banner-inner",
        ),
    )


def _invite_panel(club, player_id):
    """Only the club's admin sees this -- invites are the only way into a
    club now (see backend/services/club_invites.py), so this is the one
    place membership actually grows from."""
    if not player_id or str(club.get("club_admin")) != player_id:
        return None

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Invite a Player"),
            html.Div(
                className="t3g-panel-body",
                children=[
                    html.P(
                        "Ask them for their Player ID -- it's on their My Account page.",
                        className="t3g-empty-state mb-2",
                    ),
                    dbc.Input(
                        id="club-invite-player-id",
                        placeholder="Player ID",
                        type="text",
                        className="mb-2",
                    ),
                    html.Div(id="club-invite-send-error", className="text-danger mt-2"),
                    html.Button(
                        "Send Invite",
                        id="club-invite-send",
                        className="t3g-panel-action-button mt-2",
                        n_clicks=0,
                    ),
                ],
            ),
        ],
    )


def _course_label(course):
    # Same field names/format as home.py's and my_account.py's course
    # pickers -- kept as its own copy per page rather than a shared
    # import, matching how this app already duplicates it in both of
    # those rather than introducing a components module for one helper.
    label = course["club_name"]
    if course.get("course_name"):
        label += f" — {course['course_name']}"
    location = course.get("county") or course.get("postcode")
    return f"{label} ({location})" if location else label


def _tournament_round_row(index, group_size=_DEFAULT_GROUP_SIZE):
    """One row of the Create Tournament modal's round list -- date, course,
    and tees, all keyed by a stable per-row index so add/remove and the
    course->tee cascade (edit_tournament_rounds / load_tournament_round_tees
    below) can address a specific row via pattern-matching ids without the
    others. Styled as its own soft rounded tile (same treatment as the
    .t3g-score-button fields on the live round page and .t3g-tournament-item
    tiles elsewhere on this page) rather than a bordered table row -- a
    numbered label replaces the column-header row that used to sit above
    the list, so each tile is self-labelled instead of needing to line up
    against headings above it.

    The course field starts with empty options and fills in as you type
    (search_tournament_round_course_options below) instead of being handed
    a full course list -- this used to be preloaded once at page load via
    a shared options store so every row (including ones added later) could
    reuse it without a fresh API call per row, but that meant loading this
    page at all paid for fetching the entire course catalog regardless of
    whether the modal was even opened. Same fix home.py's round-upload
    course picker already got for the same reason."""
    return html.Div(
        id={"type": "tournament-round-row", "index": index},
        className="t3g-tournament-round-row",
        children=[
            html.Span(f"Round {index + 1}", className="t3g-tournament-round-number"),
            dcc.DatePickerSingle(
                id={"type": "tournament-round-date", "index": index},
                placeholder="Date",
                display_format="D MMM YYYY",
                className="t3g-tournament-round-date",
            ),
            dcc.Dropdown(
                id={"type": "tournament-round-course", "index": index},
                options=[],
                placeholder="Search course...",
                searchable=True,
                className="t3g-tournament-round-course",
            ),
            dcc.Dropdown(
                id={"type": "tournament-round-tee", "index": index},
                placeholder="Tees",
                disabled=True,
                className="t3g-tournament-round-tee",
            ),
            dcc.Dropdown(
                id={"type": "tournament-round-group-size", "index": index},
                options=_GROUP_SIZE_OPTIONS,
                value=group_size,
                clearable=False,
                className="t3g-tournament-round-group-size",
            ),
            html.Button(
                "Remove",
                id={"type": "tournament-round-remove", "index": index},
                className="t3g-panel-action-button t3g-panel-action-button--secondary t3g-tournament-round-remove",
                n_clicks=0,
            ),
        ],
    )


def _tournament_item(tournament, slug):
    """One solid tile per tournament -- same visual treatment as Your
    Clubs' .t3g-club-item tiles on the home page (light tile, bold title,
    hover lift), just with a second, muted line for round/entrant counts
    and a live badge while the tournament's underway. Links through to the
    tournament's own page (info/entrants/leaderboard)."""
    rounds = tournament.get("rounds", [])
    entrant_count = sum(1 for e in tournament.get("entrants", []) if e.get("status") == "confirmed")

    title_children = [html.Span(tournament.get("name", "Tournament"), className="t3g-tournament-item-title")]
    if tournament.get("status") == "in_progress":
        title_children.append(live_badge())

    return dcc.Link(
        href=f"/clubs/{slug}/tournaments/{tournament['id']}",
        className="t3g-tournament-item",
        children=[
            html.Div(title_children, className="t3g-tournament-item-title-group"),
            html.Div(
                f"{len(rounds)} round" + ("" if len(rounds) == 1 else "s")
                + " · "
                + f"{entrant_count} entrant" + ("" if entrant_count == 1 else "s"),
                className="t3g-tournament-item-meta",
            ),
        ],
    )


def _tournaments_panel(is_admin, tournaments, slug):
    body = (
        html.Div([_tournament_item(t, slug) for t in tournaments], className="t3g-tournament-list")
        if tournaments
        else html.P("No tournaments yet.", className="t3g-empty-state")
    )

    action = (
        html.Button(
            "Create Tournament",
            id="tournament-create-button",
            className="t3g-panel-action-button",
            n_clicks=0,
        )
        if is_admin
        else None
    )

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Tournaments", action=action),
            html.Div(body, className="t3g-panel-body"),
        ],
    )


def _handicap_stepper(id_prefix, label):
    """Same +/- stepper live_round.py's Shots/Putts entry uses
    (.t3g-stepper / -button / -value / -label / -row / -col, all defined
    once in live_round.css and shared globally via Dash's assets/ folder --
    nothing to redeclare here), laid out horizontally instead of
    vertically via the .t3g-stepper--horizontal modifier in club.css.
    adjust_tournament_min_handicap/adjust_tournament_max_handicap below
    hold the actual value in {id_prefix}-store; the display div just
    mirrors it as text."""
    return html.Div(
        className="t3g-stepper-col",
        children=[
            html.Div(label, className="t3g-stepper-label"),
            html.Div(
                className="t3g-stepper t3g-stepper--horizontal",
                children=[
                    html.Button("–", id=f"{id_prefix}-minus", className="t3g-stepper-button", n_clicks=0),
                    html.Div("–", id=f"{id_prefix}-display", className="t3g-stepper-value"),
                    html.Button("+", id=f"{id_prefix}-plus", className="t3g-stepper-button", n_clicks=0),
                ],
            ),
            dcc.Store(id=f"{id_prefix}-store", data=None),
        ],
    )


def _tournament_modal():
    return dbc.Modal(
        id="tournament-modal",
        is_open=False,
        size="lg",
        children=[
            dbc.ModalHeader(dbc.ModalTitle("Create Tournament")),
            dbc.ModalBody(
                children=[
                    html.Div(
                        className="t3g-modal-section",
                        children=[
                            dbc.Input(
                                id="tournament-name-input",
                                placeholder="Tournament name",
                                type="text",
                                className="mb-2",
                            ),
                            dcc.Dropdown(
                                id="tournament-format-input",
                                options=_TOURNAMENT_FORMAT_OPTIONS,
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
                                        id="tournament-entry-mode-input",
                                        options=_TOURNAMENT_ENTRY_MODE_OPTIONS,
                                        value="self",
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
                                            _handicap_stepper("tournament-min-handicap", "Min"),
                                            _handicap_stepper("tournament-max-handicap", "Max"),
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
                                id="tournament-grouping-method-input",
                                options=_TOURNAMENT_GROUPING_METHOD_OPTIONS,
                                value="random",
                                className="t3g-tournament-entry-mode",
                            ),
                        ],
                    ),
                    html.Div(
                        className="t3g-modal-section",
                        children=[
                            html.Label("Rounds", className="t3g-modal-label t3g-tournament-rounds-label"),
                            html.Div(
                                id="tournament-rounds-container",
                                children=[_tournament_round_row(0)],
                            ),
                            html.Button(
                                "+ Add Round",
                                id="tournament-add-round",
                                className="t3g-panel-action-button t3g-panel-action-button--secondary mt-2",
                                n_clicks=0,
                            ),
                        ],
                    ),
                    html.Div(id="tournament-error", className="text-danger mt-3"),
                ],
            ),
            dbc.ModalFooter(
                [
                    dbc.Button("Cancel", id="tournament-cancel", color="secondary"),
                    dbc.Button("Create Tournament", id="tournament-submit", color="primary"),
                ]
            ),
        ],
    )


def _not_found_page():
    return html.Div(
        className="t3g-page",
        children=html.Div(
            className="t3g-panel",
            children=html.Div(
                html.P("Club not found.", className="t3g-empty-state"),
                className="t3g-panel-body",
            ),
        ),
    )


def layout(slug=None, **kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="club-redirect-signin", refresh=True)

    if not slug:
        return _not_found_page()

    club_resp = requests.get(f"{API_BASE_URL}/clubs/slug/{slug}")
    if club_resp.status_code != 200:
        return _not_found_page()
    club = club_resp.json()

    is_admin = bool(player_id) and str(club.get("club_admin")) == player_id

    directory_resp = requests.get(f"{API_BASE_URL}/handicaps/club/{club['id']}/latest")
    directory = directory_resp.json() if directory_resp.status_code == 200 else []

    tournaments_resp = requests.get(f"{API_BASE_URL}/tournaments/club/{club['id']}")
    tournaments = tournaments_resp.json() if tournaments_resp.status_code == 200 else []

    return html.Div(
        # t3g-club-page scopes the more compact panel spacing in club.css
        # -- .t3g-panel/-navbar/-body are shared with every other page, so
        # those overrides are kept local to this one rather than tightening
        # spacing app-wide.
        className="t3g-page t3g-club-page",
        children=[
            dcc.Store(id="club-id-store", data=club["id"]),
            _admin_banner(is_admin),
            _invite_panel(club, player_id),
            html.Div(
                className="t3g-panel-grid",
                children=[
                    html.Div(
                        className="t3g-panel",
                        children=[
                            build_panel_navbar(
                                "Player Directory",
                                action=[
                                    html.Button(
                                        "Name",
                                        id="club-directory-sort-name",
                                        className=_SORT_BUTTON_ACTIVE,
                                        n_clicks=0,
                                    ),
                                    html.Button(
                                        "Handicap",
                                        id="club-directory-sort-handicap",
                                        className=_SORT_BUTTON_BASE,
                                        n_clicks=0,
                                    ),
                                ],
                            ),
                            dcc.Store(id="club-directory-store", data=directory),
                            html.Div(
                                id="club-directory-content",
                                className="t3g-panel-body",
                                children=_directory_table(directory, "name"),
                            ),
                        ],
                    ),
                    _tournaments_panel(is_admin, tournaments, slug),
                ],
            ),
            _tournament_modal(),
            dcc.Location(id="tournament-redirect", refresh=True),
            dbc.Modal(
                id="club-invite-sent-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Invite Sent")),
                    dbc.ModalBody(id="club-invite-sent-modal-body"),
                    dbc.ModalFooter(dbc.Button("OK", id="club-invite-sent-ok", color="primary")),
                ],
            ),
        ],
    )


@callback(
    Output("club-directory-content", "children"),
    Output("club-directory-sort-name", "className"),
    Output("club-directory-sort-handicap", "className"),
    Input("club-directory-sort-name", "n_clicks"),
    Input("club-directory-sort-handicap", "n_clicks"),
    State("club-directory-store", "data"),
    prevent_initial_call=True,
)
def sort_club_directory(name_clicks, handicap_clicks, directory):
    triggered_id = dash.ctx.triggered_id
    sort_by = "handicap" if triggered_id == "club-directory-sort-handicap" else "name"
    table = _directory_table(directory or [], sort_by)

    if sort_by == "handicap":
        return table, _SORT_BUTTON_BASE, _SORT_BUTTON_ACTIVE
    return table, _SORT_BUTTON_ACTIVE, _SORT_BUTTON_BASE


@callback(
    Output("club-invite-send-error", "children"),
    Output("club-invite-sent-modal", "is_open"),
    Output("club-invite-sent-modal-body", "children"),
    Input("club-invite-send", "n_clicks"),
    State("club-invite-player-id", "value"),
    State("club-id-store", "data"),
    prevent_initial_call=True,
)
def send_club_invite_callback(n_clicks, invitee_id, club_id):
    if not invitee_id or not invitee_id.strip():
        return "Enter a player ID.", False, dash.no_update

    player_id = session.get("player_id")
    response = requests.post(
        f"{API_BASE_URL}/club-invites/",
        json={"club_id": club_id, "inviter_id": player_id, "invitee_id": invitee_id.strip()},
    )

    if response.status_code == 201:
        invitee = response.json().get("invitee") or {}
        invitee_label = (
            invitee.get("nickname")
            or f"{invitee.get('first_name', '')} {invitee.get('surname', '')}".strip()
            or "that player"
        )
        return "", True, f"Your invite to {invitee_label} has been sent."

    try:
        payload = response.json()
        detail = payload.get("detail", "Couldn't send that invite.")
        if not isinstance(detail, str):
            detail = "That doesn't look like a valid player ID."
    except ValueError:
        detail = "Couldn't send that invite."
    return detail, False, dash.no_update


@callback(
    Output("club-invite-sent-modal", "is_open", allow_duplicate=True),
    Output("club-invite-player-id", "value"),
    Input("club-invite-sent-ok", "n_clicks"),
    prevent_initial_call=True,
)
def close_club_invite_sent_modal(n_clicks):
    return False, ""


@callback(
    Output("tournament-rounds-container", "children"),
    Input("tournament-add-round", "n_clicks"),
    Input({"type": "tournament-round-remove", "index": ALL}, "n_clicks"),
    State("tournament-rounds-container", "children"),
    prevent_initial_call=True,
)
def edit_tournament_rounds(add_clicks, remove_clicks_list, current_rows):
    triggered_id = dash.ctx.triggered_id
    current_rows = current_rows or []

    if triggered_id == "tournament-add-round":
        next_index = max((row["props"]["id"]["index"] for row in current_rows), default=-1) + 1
        return current_rows + [_tournament_round_row(next_index)]

    # A brand-new remove button (from a row just added above) can make
    # Dash re-fire this callback on its own, with triggered_id pointing at
    # that new button even though nobody clicked it -- any(remove_clicks_list)
    # tells a real click (n_clicks >= 1) apart from that phantom trigger.
    # Same guard style as accept_round_invite/decline_round_invite in
    # home.py for the exact same reason.
    if (
        isinstance(triggered_id, dict)
        and triggered_id.get("type") == "tournament-round-remove"
        and any(remove_clicks_list or [])
    ):
        # Always keep at least one round -- a tournament with zero rounds
        # isn't submittable anyway (see handle_tournament_modal).
        if len(current_rows) <= 1:
            return dash.no_update
        removed_index = triggered_id["index"]
        return [row for row in current_rows if row["props"]["id"]["index"] != removed_index]

    return dash.no_update


@callback(
    Output({"type": "tournament-round-course", "index": MATCH}, "options"),
    Input({"type": "tournament-round-course", "index": MATCH}, "search_value"),
    Input({"type": "tournament-round-course", "index": MATCH}, "value"),
    State({"type": "tournament-round-course", "index": MATCH}, "options"),
    prevent_initial_call=True,
)
def search_tournament_round_course_options(search_value, selected_course_id, current_options):
    # Same fix, same reasoning, as home.py's search_course_options -- see
    # that function's docstring. MATCH keeps each round row's search bound
    # only to its own course field, never another row's.
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
    Output({"type": "tournament-round-tee", "index": MATCH}, "options"),
    Output({"type": "tournament-round-tee", "index": MATCH}, "disabled"),
    Output({"type": "tournament-round-tee", "index": MATCH}, "value"),
    Input({"type": "tournament-round-course", "index": MATCH}, "value"),
    prevent_initial_call=True,
)
def load_tournament_round_tees(course_id):
    # Same cache-then-import lookup home.py's round-upload modal uses --
    # MATCH means each round row's course dropdown only ever drives its
    # own tee dropdown, never anyone else's.
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
    Output("tournament-min-handicap-store", "data"),
    Output("tournament-min-handicap-display", "children"),
    Input("tournament-min-handicap-plus", "n_clicks"),
    Input("tournament-min-handicap-minus", "n_clicks"),
    State("tournament-min-handicap-store", "data"),
    prevent_initial_call=True,
)
def adjust_tournament_min_handicap(plus_clicks, minus_clicks, current):
    return _adjust_handicap_stepper(
        dash.ctx.triggered_id, "tournament-min-handicap-plus", "tournament-min-handicap-minus", current
    )


@callback(
    Output("tournament-max-handicap-store", "data"),
    Output("tournament-max-handicap-display", "children"),
    Input("tournament-max-handicap-plus", "n_clicks"),
    Input("tournament-max-handicap-minus", "n_clicks"),
    State("tournament-max-handicap-store", "data"),
    prevent_initial_call=True,
)
def adjust_tournament_max_handicap(plus_clicks, minus_clicks, current):
    return _adjust_handicap_stepper(
        dash.ctx.triggered_id, "tournament-max-handicap-plus", "tournament-max-handicap-minus", current
    )


@callback(
    Output("tournament-modal", "is_open"),
    Output("tournament-error", "children"),
    Output("tournament-redirect", "href"),
    Output("tournament-rounds-container", "children", allow_duplicate=True),
    Output("tournament-name-input", "value"),
    Output("tournament-format-input", "value"),
    Output("tournament-entry-mode-input", "value"),
    Output("tournament-grouping-method-input", "value"),
    Output("tournament-min-handicap-store", "data", allow_duplicate=True),
    Output("tournament-min-handicap-display", "children", allow_duplicate=True),
    Output("tournament-max-handicap-store", "data", allow_duplicate=True),
    Output("tournament-max-handicap-display", "children", allow_duplicate=True),
    Input("tournament-create-button", "n_clicks"),
    Input("tournament-cancel", "n_clicks"),
    Input("tournament-submit", "n_clicks"),
    State("tournament-name-input", "value"),
    State("tournament-format-input", "value"),
    State("tournament-entry-mode-input", "value"),
    State("tournament-grouping-method-input", "value"),
    State("tournament-min-handicap-store", "data"),
    State("tournament-max-handicap-store", "data"),
    State({"type": "tournament-round-date", "index": ALL}, "date"),
    State({"type": "tournament-round-course", "index": ALL}, "value"),
    State({"type": "tournament-round-tee", "index": ALL}, "value"),
    State({"type": "tournament-round-group-size", "index": ALL}, "value"),
    State("club-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_tournament_modal(
    open_clicks, cancel_clicks, submit_clicks,
    name, format_value, entry_mode, grouping_method, min_handicap, max_handicap,
    round_dates, round_courses, round_tees, round_group_sizes,
    club_id, current_pathname,
):
    triggered_id = dash.ctx.triggered_id
    no_update_rest = (dash.no_update,) * 9

    if triggered_id == "tournament-create-button":
        # Fresh modal every time it's opened -- one blank round row, no
        # leftover name/format/entry/handicap-range settings from a
        # previous cancelled attempt.
        return (
            True, "", dash.no_update, [_tournament_round_row(0)],
            None, None, "self", "random", None, "–", None, "–",
        )

    if triggered_id == "tournament-cancel":
        return (False, "", dash.no_update) + no_update_rest

    if triggered_id == "tournament-submit":
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

        player_id = session.get("player_id")
        response = requests.post(
            f"{API_BASE_URL}/tournaments/",
            json={
                "club_id": club_id,
                "admin_id": player_id,
                "name": name.strip(),
                "format": format_value,
                "entry_mode": entry_mode or "self",
                "grouping_method": grouping_method or "random",
                "min_handicap": min_handicap,
                "max_handicap": max_handicap,
                "rounds": rounds_payload,
            },
        )

        if response.status_code == 201:
            # dcc.Location only reloads when the value it's given differs
            # from what's already loaded -- appending a cache-busting query
            # string guarantees that even though the pathname itself
            # (/clubs/<slug>) doesn't change, same fix as my_account.py and
            # friends.py's _refresh_href(). Reads the real current path off
            # Dash Pages' own router state (_pages_location) instead of a
            # separately-stored slug -- one less thing that can go stale or
            # end up None and send you to a URL that doesn't resolve.
            return (False, "", f"{current_pathname}?_r={time.time()}") + no_update_rest

        try:
            detail = response.json().get("detail", "Couldn't create that tournament.")
            if not isinstance(detail, str):
                detail = "Couldn't create that tournament."
        except ValueError:
            detail = "Couldn't create that tournament."
        return (True, detail, dash.no_update) + no_update_rest

    return (dash.no_update, dash.no_update, dash.no_update) + no_update_rest