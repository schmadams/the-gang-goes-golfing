# target path: frontend/src/pages/play.py (new file -- replaces live_round.py, delete that file)
"""
Play is the "start and see rounds" hub -- what used to be the bare Live
Round page (a dead end unless you already had a round in progress) is now
a proper landing page with two tabs: Live (Start New Round, plus every
round you're actively playing right now -- casual and tournament, see
rounds.py's tournament_scope split) and Scheduled (every upcoming
tournament tee time you're grouped into, across every club, that hasn't
been started yet -- see backend/services/tournament_tee_times.py's
list_scheduled_tee_times_for_player).

Reached with no query params, this renders the hub. Reached with
?round_id=... (from a live round card's Continue link, a tournament's
Start/Continue Live Round link, or round_signoff.py's reject redirect),
it renders the actual scorecard for that one round instead -- all of that
scoring machinery (score entry modal, hole-by-hole/full view toggle,
Finish/Scrap/Leave/NR) is untouched from the old live_round.py, just
renamed at the file/route level (see layout()'s own docstring for the
exact dispatch). Every internal link that used to fall back on "no
round_id -> whichever round happens to be active" now always passes an
explicit round_id instead (toggle_upload_round_modal and
handle_continue_round below, tournament.py's handle_start_live_round,
round_signoff.py's reject redirect) -- with two independent live-round
pools now possible (casual + tournament), that fallback would be
ambiguous, so the hub is what "no round_id" means now, full stop.

Start New Round (modal + its callbacks) moved here wholesale from
my_profile.py -- same ids, same "small per-page copies" convention as
the rest of the app, just relocated now that Play (not My Profile) is
the one place to start a round. See my_profile.py's own module docstring
for the other half of that move.
"""
from contextlib import contextmanager
from datetime import datetime

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
from components.scorecard import (
    format_handicap,
    history_score_mark_class,
    live_badge,
    round_header_label,
    tournament_round_badge,
)
from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/play", name="Play")

_FAIRWAY_RADIO_TO_BOOL = {"yes": True, "no": False}
_FAIRWAY_BOOL_TO_RADIO = {True: "yes", False: "no"}

# Same literal class strings as tournament.py's _TAB_BUTTON_BASE/_ACTIVE --
# duplicated rather than imported across pages (these two modules don't
# otherwise depend on each other, and Dash Pages modules aren't really
# meant to import from one another -- see components/live_scorecard.py's
# module docstring for why that specifically breaks page registration) --
# reused for both this page's own Live/Scheduled subnav and the
# back-to-tournament subnav shown when scoring a tournament round.
_TAB_BUTTON_BASE = "t3g-tournament-tab"
_TAB_BUTTON_ACTIVE = "t3g-tournament-tab t3g-tournament-tab--active"


@contextmanager
def _timed(label: str):
    """
    Logs how long a call to our own API took, tagged "own API" so it's
    obvious from the console which layer (frontend->backend, vs the
    backend's own external/database calls, logged separately in
    backend/services/courses.py) any slowness is actually coming from.
    """
    import time as _time

    start = _time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (_time.perf_counter() - start) * 1000
        print(f"[TIMING] own API      {elapsed_ms:8.1f}ms  {label}")


def _club_label(club):
    # Same location-suffix idea as courses' own _course_label, but for the
    # club-only step of the Start New Round club -> course -> tees flow --
    # a club option here has no course_name of its own (see ClubOption /
    # search_local_clubs in backend/services/courses.py), just whichever
    # course row it was deduped from.
    label = club["club_name"]
    location = club.get("county") or club.get("postcode")
    return f"{label} ({location})" if location else label


def _play_subnav(active):
    return html.Div(
        className="t3g-tournament-subnav",
        children=html.Div(
            className="t3g-tournament-subnav-inner",
            children=html.Div(
                className="t3g-tournament-tabs",
                children=[
                    dcc.Link(
                        "Live",
                        href="/play",
                        className=_TAB_BUTTON_ACTIVE if active == "live" else _TAB_BUTTON_BASE,
                    ),
                    dcc.Link(
                        "Scheduled",
                        href="/play?tab=scheduled",
                        className=_TAB_BUTTON_ACTIVE if active == "scheduled" else _TAB_BUTTON_BASE,
                    ),
                ],
            ),
        ),
    )


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


def _round_scorecard_card(round_data, player_rows):
    """Renders one round as a mini traditional scorecard: hole numbers
    across the top, a par row, and one player row per entry in
    player_rows (a single row for a solo round, one row per participant
    for a live round with other people in it), with the same birdie/bogey
    marks used on the scorecard itself, plus OUT/IN/TOT/HCP/NET summary
    columns per row. Duplicated from my_profile.py's own copy rather than
    cross-imported -- same "small per-page copies" convention as the rest
    of this app.

    Each entry in player_rows is {"initial", "label", "holes", "handicap"}
    -- "holes" is that player's own list of HoleScoreResponse-shaped dicts,
    par/yardage included. Par (for the shared par row) is read off the
    first row, since everyone in the same round shares the same course."""
    reference_holes = {h["hole_number"]: h for h in (player_rows[0]["holes"] if player_rows else [])}
    front9 = [reference_holes.get(n, {"hole_number": n}) for n in range(1, 10)]
    back9 = [reference_holes.get(n, {"hole_number": n}) for n in range(10, 19)]

    def _sum_par(hole_subset):
        pars = [h.get("par") for h in hole_subset if h.get("par") is not None]
        return sum(pars) if pars else None

    def _sum_strokes(hole_subset):
        strokes = [h.get("strokes") for h in hole_subset if h.get("strokes") is not None]
        return sum(strokes) if strokes else None

    out_par, in_par = _sum_par(front9), _sum_par(back9)
    tot_par = out_par + in_par if out_par is not None and in_par is not None else None

    def _hole_number_cells(hole_subset):
        return [html.Th(str(h["hole_number"])) for h in hole_subset]

    def _par_cells(hole_subset):
        return [html.Td(h.get("par") if h.get("par") is not None else "—") for h in hole_subset]

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
            + _par_cells(front9)
            + [html.Td(out_par if out_par is not None else "—", className="t3g-history-summary-cell")]
            + _par_cells(back9)
            + [
                html.Td(in_par if in_par is not None else "—", className="t3g-history-summary-cell"),
                html.Td(tot_par if tot_par is not None else "—", className="t3g-history-summary-cell"),
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    def _build_player_row(row):
        holes_by_number = {h["hole_number"]: h for h in row["holes"]}
        row_front9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(1, 10)]
        row_back9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(10, 19)]
        out_strokes, in_strokes = _sum_strokes(row_front9), _sum_strokes(row_back9)
        total_strokes = (
            out_strokes + in_strokes
            if out_strokes is not None and in_strokes is not None
            else None
        )

        handicap = row.get("handicap")
        hcp_display = format_handicap(handicap)
        net_display = round(total_strokes - handicap) if (handicap is not None and total_strokes is not None) else "—"

        return html.Tr(
            className="t3g-history-player-row",
            children=(
                [
                    html.Td(
                        html.Div(
                            [
                                html.Div(row["initial"], className="t3g-history-player-avatar"),
                                html.Span(row["label"]),
                            ],
                            className="t3g-history-player-cell",
                        )
                    )
                ]
                + _score_cells(row_front9)
                + [html.Td(out_strokes if out_strokes is not None else "—", className="t3g-history-summary-cell")]
                + _score_cells(row_back9)
                + [
                    html.Td(in_strokes if in_strokes is not None else "—", className="t3g-history-summary-cell"),
                    html.Td(total_strokes if total_strokes is not None else "—", className="t3g-history-summary-cell"),
                    html.Td(hcp_display, className="t3g-history-summary-cell"),
                    html.Td(net_display, className="t3g-history-summary-cell"),
                ]
            ),
        )

    is_live = round_data.get("status") == "in_progress"
    is_tournament = bool(round_data.get("tournament_id"))
    header_children = [html.Span(round_header_label(round_data), className="t3g-round-card-title")]

    badges = []
    if is_tournament:
        badges.append(tournament_round_badge())
    if is_live:
        badges.append(live_badge())
    if badges:
        header_children.append(html.Div(badges, className="t3g-round-card-header-actions"))

    return html.Div(
        className="t3g-round-card",
        children=[
            html.Div(
                header_children,
                className="t3g-round-card-header",
            ),
            html.Div(
                className="t3g-history-scorecard-wrap",
                children=html.Table(
                    className="t3g-history-scorecard-table",
                    children=[
                        html.Thead([header_row, par_row]),
                        html.Tbody([_build_player_row(row) for row in player_rows]),
                    ],
                ),
            ),
        ],
    )


def _live_tab_content(player_id):
    """Start New Round + every round this player is actively playing
    right now. A player can have a casual round *and* a tournament round
    live at the same time (see backend/services/rounds.py's
    tournament_scope split) -- both show, as separate cards, sourced the
    same way my_profile.py's old live-round panel used to (filtering the
    Rounds History endpoint down to status="in_progress"), just relocated
    here now that Play owns "what am I playing right now"."""
    with _timed(f"GET /rounds/player/{player_id}"):
        rounds_resp = requests.get(f"{API_BASE_URL}/rounds/player/{player_id}")
    rounds_history = rounds_resp.json() if rounds_resp.status_code == 200 else []
    live_rounds = [r for r in rounds_history if r.get("status") == "in_progress"]

    # Only needed for a live round's own scorecard row -- skip the call
    # entirely when there's nothing live to render one for.
    player_info = {"initial": "Y", "label": "You"}
    current_handicap = None
    if live_rounds:
        with _timed(f"GET /players/{player_id}"):
            player_resp = requests.get(f"{API_BASE_URL}/players/{player_id}")
        player = player_resp.json() if player_resp.status_code == 200 else {}
        player_label = player.get("nickname") or player.get("first_name") or "You"
        player_initial = player_label[0].upper() if player_label else "Y"
        player_info = {"initial": player_initial, "label": player_label}

        with _timed(f"GET /handicaps/player/{player_id}/current"):
            current_handicap_resp = requests.get(f"{API_BASE_URL}/handicaps/player/{player_id}/current")
        current_handicap = (
            current_handicap_resp.json().get("handicap") if current_handicap_resp.status_code == 200 else None
        )

    live_round_sections = []
    for live_round in live_rounds:
        is_tournament_round = bool(live_round.get("tournament_id"))

        # The Rounds History endpoint only ever returns *your* scorecard
        # (RoundSummaryResponse is deliberately single-player), so a round
        # with other people in it needs the full detail lookup
        # (RoundDetailResponse) to get everyone's holes.
        with _timed(f"GET /rounds/{live_round['id']}"):
            live_round_detail_resp = requests.get(
                f"{API_BASE_URL}/rounds/{live_round['id']}", params={"viewer_player_id": player_id}
            )
        live_round_detail = live_round_detail_resp.json() if live_round_detail_resp.status_code == 200 else None

        if live_round_detail and live_round_detail.get("players"):
            live_round_player_rows = []
            for participant in live_round_detail["players"]:
                participant_id = str(participant.get("player_id"))
                label = (
                    participant.get("nickname")
                    or f"{participant.get('first_name', '')} {participant.get('surname', '')}".strip()
                    or "Player"
                )
                if participant_id == player_id:
                    label = f"{label} (you)"
                    participant_handicap = current_handicap
                else:
                    with _timed(f"GET /handicaps/player/{participant_id}/current"):
                        participant_handicap_resp = requests.get(
                            f"{API_BASE_URL}/handicaps/player/{participant_id}/current"
                        )
                    participant_handicap = (
                        participant_handicap_resp.json().get("handicap")
                        if participant_handicap_resp.status_code == 200
                        else None
                    )
                live_round_player_rows.append(
                    {
                        "initial": label[0].upper() if label else "?",
                        "label": label,
                        "holes": participant.get("holes") or [],
                        "handicap": participant_handicap,
                    }
                )
        else:
            live_round_player_rows = [
                {
                    "initial": player_info["initial"],
                    "label": player_info["label"],
                    "holes": live_round.get("holes") or [],
                    "handicap": live_round.get("handicap"),
                }
            ]

        panel_title = "Tournament Round" if is_tournament_round else "Live Round"
        live_round_sections.append(
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar(
                        panel_title,
                        action=dcc.Link(
                            "Continue Round",
                            href=f"/play?round_id={live_round['id']}",
                            className="t3g-panel-action-button",
                            style={"textDecoration": "none"},
                        ),
                    ),
                    html.Div(
                        _round_scorecard_card(live_round_detail or live_round, live_round_player_rows),
                        className="t3g-panel-body",
                    ),
                ],
            )
        )

    if not live_round_sections:
        live_round_sections = [
            html.P(
                "No live rounds right now -- start one below.",
                className="t3g-empty-state t3g-play-empty-state",
            )
        ]

    return [
        html.Div(
            className="t3g-panel",
            children=[
                build_panel_navbar(
                    "Start a Round",
                    action=html.Button(
                        "Start New Round",
                        id="upload-round-button",
                        className="t3g-panel-action-button",
                    ),
                ),
                html.Div(
                    "Casual round, with or without friends, or a round tagged to one of your clubs.",
                    className="t3g-panel-body t3g-empty-state",
                ),
            ],
        ),
        *live_round_sections,
    ]


def _scheduled_player_names(players, viewer_player_id):
    names = []
    for p in players:
        label = p.get("nickname") or f"{p.get('first_name', '')} {p.get('surname', '')}".strip() or "Player"
        if str(p.get("player_id")) == viewer_player_id:
            label = f"{label} (you)"
        names.append(label)
    return names


def _format_scheduled_date(date_str, time_str):
    # round_date is a plain "YYYY-MM-DD" date, tee_time a plain "HH:MM[:SS]"
    # time -- both come straight off the DB as strings, no timezone
    # conversion needed since these are always local-to-the-course times.
    # Day-of-month is built by hand rather than via strftime's platform-
    # dependent %-d/%#d flag -- same cross-platform fix as club.py/home.py's
    # own _format_feed_timestamp (that one raised ValueError on Windows).
    if not date_str:
        return time_str or ""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return f"{date_str} {time_str or ''}".strip()
    date_label = f"{d.day} {d.strftime('%b %Y')}"
    return f"{date_label}, {time_str}" if time_str else date_label


def _scheduled_card(entry, player_id):
    course_bits = [b for b in [entry.get("venue_name"), entry.get("course_name")] if b]
    course_label = " — ".join(course_bits) if course_bits else None
    tee_label = f"{entry['tee_name']} tees" if entry.get("tee_name") else None
    meta_line = " · ".join(b for b in [course_label, tee_label] if b)

    player_names = _scheduled_player_names(entry.get("players") or [], player_id)
    round_badge = f"Round {entry['round_number']}" if entry.get("round_number") else None

    header_children = [
        dcc.Link(
            entry.get("tournament_name") or "Tournament",
            href=f"/clubs/{entry['club_slug']}/tournaments/{entry['tournament_id']}?tab=startsheet",
            className="t3g-round-card-title",
            style={"textDecoration": "none"},
        )
    ]
    if round_badge:
        header_children.append(html.Span(round_badge, className="t3g-tournament-round-badge"))

    return html.Div(
        className="t3g-round-card t3g-play-scheduled-card",
        children=[
            html.Div(header_children, className="t3g-round-card-header"),
            html.Div(
                className="t3g-panel-body t3g-play-scheduled-body",
                children=[
                    html.Div(entry.get("club_name") or "", className="t3g-play-scheduled-club"),
                    html.Div(
                        _format_scheduled_date(entry.get("round_date"), entry.get("tee_time")),
                        className="t3g-play-scheduled-meta",
                    ),
                    (html.Div(meta_line, className="t3g-play-scheduled-meta") if meta_line else None),
                    html.Div(
                        ("Group: " + ", ".join(player_names)) if player_names else "Group not yet set",
                        className="t3g-play-scheduled-players",
                    ),
                ],
            ),
        ],
    )


def _scheduled_tab_content(player_id):
    with _timed(f"GET /tournaments/scheduled/{player_id}"):
        response = requests.get(f"{API_BASE_URL}/tournaments/scheduled/{player_id}")
    scheduled = response.json() if response.status_code == 200 else []

    if not scheduled:
        return [
            html.P(
                "No scheduled tournament rounds yet -- these show up once a club "
                "admin generates tee times for a tournament you're entered in.",
                className="t3g-empty-state t3g-play-empty-state",
            )
        ]

    return [_scheduled_card(entry, player_id) for entry in scheduled]


def _upload_round_modal(player_id):
    """Start New Round modal -- moved wholesale from my_profile.py (same
    ids, same markup) now that Play is the one place to start a round.
    Builds its own friend-picker option list rather than reusing one
    fetched elsewhere on the page, since the Live tab above doesn't
    otherwise need it.

    No club-tag dropdown here any more -- a round used to need to be
    manually tagged with a club to count toward that club's player
    comparison analysis. Now that's automatic: get_club_player_comparison
    in backend/services/rounds.py works out for itself which club(s) a
    round belongs to, by checking which of the round's players are
    members of which club, so there's nothing for whoever's starting the
    round to pick here."""
    with _timed(f"GET /friends/player/{player_id}"):
        friends_resp = requests.get(f"{API_BASE_URL}/friends/player/{player_id}")
    friends = friends_resp.json() if friends_resp.status_code == 200 else []
    friend_invite_options = [
        {"label": f.get("nickname") or f"{f.get('first_name', '')} {f.get('surname', '')}".strip(), "value": f["player_id"]}
        for f in friends
    ]

    return html.Div(
        children=[
            dbc.Modal(
                id="upload-round-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Start New Round")),
                    dbc.ModalBody(
                        [
                            # Club -> Course -> Tees, one step at a time --
                            # a club search step first (search_local_clubs,
                            # deduped by club_name) narrows the second
                            # dropdown down to just that club's own cached
                            # courses (usually just one, auto-selected below)
                            # before the tees. All three live inside one
                            # container so the manual-entry toggle can
                            # show/hide the whole group with a single Output
                            # instead of one per field.
                            html.Div(
                                id="upload-round-course-fields",
                                children=[
                                    dcc.Dropdown(
                                        id="upload-round-club",
                                        placeholder="Type to search for the club you played at",
                                        options=[],
                                        searchable=True,
                                        clearable=True,
                                        className="mb-2 t3g-course-dropdown",
                                    ),
                                    dcc.Dropdown(
                                        id="upload-round-course",
                                        placeholder="Select the course",
                                        options=[],
                                        disabled=True,
                                        className="mb-2 t3g-course-dropdown",
                                    ),
                                    dcc.Dropdown(
                                        id="upload-round-tee",
                                        placeholder="Select tees",
                                        options=[],
                                        disabled=True,
                                        className="mb-1 t3g-course-dropdown",
                                    ),
                                    html.Div(id="upload-round-tee-status", className="t3g-empty-state mt-1"),
                                ],
                            ),
                            html.Button(
                                "Can't find your course? Enter it manually",
                                id="upload-round-manual-toggle",
                                className="t3g-link-button mb-2",
                                n_clicks=0,
                            ),
                            html.Div(
                                id="upload-round-manual-fields",
                                style={"display": "none"},
                                children=[
                                    dbc.Input(
                                        id="upload-round-manual-club",
                                        placeholder="Club name",
                                        className="mb-2",
                                    ),
                                    dbc.Input(
                                        id="upload-round-manual-tee",
                                        placeholder="Tee name (e.g. White)",
                                        className="mb-2",
                                    ),
                                    dbc.Input(
                                        id="upload-round-manual-rating",
                                        placeholder="Course Rating (optional, e.g. 71.4)",
                                        type="number",
                                        className="mb-2",
                                    ),
                                    dbc.Input(
                                        id="upload-round-manual-slope",
                                        placeholder="Slope Rating (optional, e.g. 125)",
                                        type="number",
                                        className="mb-2",
                                    ),
                                    html.P(
                                        "Course/Slope Rating are usually printed on the "
                                        "scorecard next to the tee colour. They're optional, "
                                        "but without them this round can't count toward "
                                        "anyone's handicap.",
                                        className="t3g-empty-state",
                                    ),
                                    html.P(
                                        "You'll enter par, length, and stroke index for each "
                                        "hole once the round starts.",
                                        className="t3g-empty-state",
                                    ),
                                ],
                            ),
                            dcc.Store(id="upload-round-manual-mode", data=False),
                            html.Label(
                                "Add up to 3 friends to this round (optional)",
                                className="t3g-modal-label mt-2",
                            ),
                            html.P(
                                "Add some friends first to invite them to a round.",
                                className="t3g-empty-state",
                            )
                            if not friend_invite_options
                            else html.Div(
                                className="t3g-friend-picker-row",
                                children=[
                                    # Single-select on the left -- picking a
                                    # friend here moves them into the store
                                    # (add_friend_to_round below) and clears
                                    # the dropdown back to its placeholder,
                                    # rather than the usual Dash multi-select
                                    # pattern of collecting chips inside the
                                    # dropdown control itself. Selected
                                    # friends live on the right instead (see
                                    # upload-round-friends-selected), and
                                    # render_friend_picker filters them back
                                    # out of these options so the same friend
                                    # can't be picked twice.
                                    dcc.Dropdown(
                                        id="upload-round-friend-picker",
                                        placeholder="Add a friend...",
                                        options=friend_invite_options,
                                        value=None,
                                        clearable=True,
                                        className="t3g-friend-picker-dropdown",
                                    ),
                                    html.Div(
                                        id="upload-round-friends-selected",
                                        className="t3g-friend-picker-selected",
                                    ),
                                ],
                            ),
                            dcc.Store(id="upload-round-friend-options-store", data=friend_invite_options),
                            dcc.Store(id="upload-round-friends-store", data=[]),
                            html.Div(id="upload-round-error", className="text-danger mt-2"),
                            html.Div(id="upload-round-status", className="mt-2"),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button(
                                "Cancel", id="upload-round-cancel", color="secondary"
                            ),
                            dbc.Button(
                                "Continue",
                                id="upload-round-continue",
                                color="primary",
                                disabled=True,
                            ),
                        ]
                    ),
                ],
            ),
            dcc.Location(id="upload-round-redirect", refresh=True),
        ],
    )


def layout(round_id=None, view=None, tab=None, **kwargs):
    # view="full" is only ever set by pages/round_signoff.py's reject
    # redirect (/play?round_id=...&view=full) -- sends a rejected round's
    # scorecard straight to the Full Scorecard view instead of the usual
    # Hole by Hole default, so whoever's fixing it can see every hole at
    # once rather than clicking through one at a time. Any other value
    # (including the plain no-argument case) keeps the normal default --
    # see render_live_round_body's initial_view param.
    initial_view = "full" if view == "full" else "holebyhole"
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="live-round-redirect-signin", refresh=True)

    if not round_id:
        # Hub mode -- Start New Round + every round you're actively
        # playing (Live tab), or every upcoming tournament tee time
        # you're grouped into (Scheduled tab). Every internal link that
        # sends someone here to look at a *specific* round always passes
        # round_id explicitly now (see this module's own docstring) --
        # "no round_id" unambiguously means "show me the hub", not "guess
        # which of my up-to-two live rounds I meant".
        active_tab = "scheduled" if tab == "scheduled" else "live"
        content = _scheduled_tab_content(player_id) if active_tab == "scheduled" else _live_tab_content(player_id)
        return html.Div(
            className="t3g-page t3g-play-page",
            children=[_play_subnav(active_tab), *content, _upload_round_modal(player_id)],
        )

    # round_id given -- render the actual scorecard for that one round.
    # Fetched directly by id rather than through an "active round" lookup,
    # because a player can now have a casual round *and* a tournament
    # round live at the same time (see _get_active_round_id_for_player's
    # tournament_scope split in backend/services/rounds.py).
    response = requests.get(
        f"{API_BASE_URL}/rounds/{round_id}", params={"viewer_player_id": player_id}
    )

    if response.status_code != 200:
        return html.Div(
            className="t3g-page",
            children=html.Div(
                className="t3g-panel",
                children=html.Div(
                    className="t3g-panel-body",
                    children=[
                        html.P(
                            "That round doesn't exist (any more).",
                            className="t3g-empty-state",
                        ),
                        dcc.Link("Back to Play", href="/play", className="t3g-link-button"),
                    ],
                ),
            ),
        )

    round_data = response.json()
    players = round_data.get("players", [])
    pending_invites = round_data.get("pending_invites", [])

    # Guard against a stale/guessed round_id showing someone else's
    # scorecard to a player who was never part of it.
    if not any(p["player_id"] == player_id for p in players + pending_invites):
        return html.Div(
            className="t3g-page",
            children=html.Div(
                className="t3g-panel",
                children=html.Div(
                    className="t3g-panel-body",
                    children=[
                        html.P("You're not part of this round.", className="t3g-empty-state"),
                        dcc.Link("Back to Play", href="/play", className="t3g-link-button"),
                    ],
                ),
            ),
        )

    tournament_subnav = None
    if round_data.get("tournament_id") and round_data.get("club_slug"):
        tournament_subnav = _tournament_context_subnav(round_data["tournament_id"], round_data["club_slug"])

    body_children = render_live_round_body(round_data, player_id, initial_view=initial_view)
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
        # nr (No Return) counts as resolved even with no strokes value --
        # same reasoning, and same fix, as _first_unscored_hole in
        # components/live_scorecard.py.
        if hole.get("strokes") is None and not hole.get("nr"):
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
    Output("upload-round-modal", "is_open"),
    Output("upload-round-club", "value"),
    Output("upload-round-redirect", "pathname", allow_duplicate=True),
    Output("upload-round-manual-mode", "data", allow_duplicate=True),
    Output("upload-round-manual-fields", "style", allow_duplicate=True),
    Output("upload-round-course-fields", "style", allow_duplicate=True),
    Output("upload-round-friends-store", "data", allow_duplicate=True),
    Input("upload-round-button", "n_clicks"),
    Input("upload-round-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_upload_round_modal(open_clicks, cancel_clicks):
    # Resetting the club value on open (via the Output below) cascades
    # through the whole chain -- load_courses_for_club(None) clears the
    # course dropdown, which in turn triggers load_tees_for_course(None)
    # clearing the tee dropdown -- so the modal always starts fresh
    # (manual mode off, no friends carried over from last time) rather
    # than showing a stale selection.
    triggered_id = dash.ctx.triggered_id
    reset_manual = (False, {"display": "none"}, {})

    if triggered_id == "upload-round-button":
        player_id = session.get("player_id")
        with _timed(f"GET /rounds/active/{player_id}"):
            response = requests.get(f"{API_BASE_URL}/rounds/active/{player_id}")

        if response.status_code == 200:
            # Already have a casual round in progress -- go straight there
            # instead of opening the modal. The backend would reject a
            # second one anyway (one-active-round-per-player), but this
            # avoids making them fill out the form just to be told no.
            # round_id is explicit here (not just a bare "/play") so this
            # lands directly on that round's scorecard, not back on the
            # hub they just clicked away from.
            return (False, dash.no_update, f"/play?round_id={response.json()['id']}", *reset_manual, [])

        return (True, None, dash.no_update, *reset_manual, [])

    return (False, dash.no_update, dash.no_update, *reset_manual, dash.no_update)


@callback(
    Output("upload-round-manual-fields", "style", allow_duplicate=True),
    Output("upload-round-course-fields", "style", allow_duplicate=True),
    Output("upload-round-manual-mode", "data", allow_duplicate=True),
    Input("upload-round-manual-toggle", "n_clicks"),
    State("upload-round-manual-mode", "data"),
    prevent_initial_call=True,
)
def toggle_manual_entry(n_clicks, is_manual):
    is_manual = not bool(is_manual)

    if is_manual:
        return {"display": "block"}, {"display": "none"}, True

    return {"display": "none"}, {}, False


@callback(
    Output("upload-round-club", "options"),
    Input("upload-round-club", "search_value"),
    Input("upload-round-club", "value"),
    State("upload-round-club", "options"),
    prevent_initial_call=True,
)
def search_club_options(search_value, selected_club_name, current_options):
    # Same targeted-ILIKE-per-keystroke approach as the old course search
    # (search_local_clubs, not the external API, so it's cheap to call
    # this often), and the same "pin the just-picked option back in"
    # handling -- picking a club clears search_value, which would
    # otherwise wipe `options` back to [] with no entry left to render the
    # selected club's own label from.
    selected_option = next(
        (opt for opt in (current_options or []) if opt["value"] == selected_club_name),
        None,
    )

    if not search_value or len(search_value) < 2:
        return [selected_option] if selected_option else []

    with _timed(f"GET /courses/clubs?search={search_value}"):
        response = requests.get(f"{API_BASE_URL}/courses/clubs", params={"search": search_value})
    clubs = response.json() if response.status_code == 200 else []
    options = [{"label": _club_label(c), "value": c["club_name"]} for c in clubs]

    if selected_option and not any(opt["value"] == selected_option["value"] for opt in options):
        options.append(selected_option)

    return options


@callback(
    Output("upload-round-course", "options"),
    Output("upload-round-course", "value"),
    Output("upload-round-course", "disabled"),
    Input("upload-round-club", "value"),
    prevent_initial_call=True,
)
def load_courses_for_club(club_name):
    if not club_name:
        return [], None, True

    with _timed(f"GET /courses/by-club?club_name={club_name}"):
        response = requests.get(f"{API_BASE_URL}/courses/by-club", params={"club_name": club_name})
    courses = response.json() if response.status_code == 200 else []
    options = [{"label": c.get("course_name") or "Main Course", "value": c["id"]} for c in courses]

    # Most clubs only have one cached course -- skip making the player
    # pick from a dropdown that only ever has one thing in it.
    auto_value = options[0]["value"] if len(options) == 1 else None

    return options, auto_value, False


@callback(
    Output("upload-round-tee", "options"),
    Output("upload-round-tee", "disabled"),
    Output("upload-round-tee-status", "children"),
    Output("upload-round-error", "children"),
    Input("upload-round-course", "value"),
    prevent_initial_call=True,
)
def load_tees_for_course(course_id):
    if not course_id:
        return [], True, "", ""

    # Fetches the cached scorecard, importing it from the live API first if
    # this is the first time anyone's picked this course -- the only place
    # in the round-upload flow that can spend one of our monthly API
    # requests, and only ever once per course. This call can be much slower
    # than the others above on a cache miss (hits the external API + several
    # DB writes) -- watch this line specifically when diagnosing slowness.
    with _timed(f"POST /courses/{course_id}/scorecard"):
        response = requests.post(f"{API_BASE_URL}/courses/{course_id}/scorecard")

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", "Couldn't load tees for that course.")
        except ValueError:
            detail = "Couldn't load tees for that course."
        return [], True, "", detail

    course = response.json()
    tees = course.get("tees", [])

    if not tees:
        return [], True, "No tee data available for this course yet.", ""

    tee_options = [
        {
            "label": f"{tee['name']} tees" + (f" (Par {tee['par']})" if tee.get("par") else ""),
            "value": tee["id"],
        }
        for tee in tees
    ]

    return tee_options, False, "", ""


@callback(
    Output("upload-round-continue", "disabled"),
    Input("upload-round-course", "value"),
    Input("upload-round-tee", "value"),
    Input("upload-round-manual-mode", "data"),
    Input("upload-round-manual-club", "value"),
    Input("upload-round-manual-tee", "value"),
    prevent_initial_call=True,
)
def toggle_continue_button(course_id, tee_id, is_manual, manual_club, manual_tee):
    if is_manual:
        return not (manual_club and manual_tee)
    return not (course_id and tee_id)


@callback(
    Output("upload-round-status", "children"),
    Output("upload-round-redirect", "pathname", allow_duplicate=True),
    Input("upload-round-continue", "n_clicks"),
    State("upload-round-course", "value"),
    State("upload-round-tee", "value"),
    State("upload-round-manual-mode", "data"),
    State("upload-round-manual-club", "value"),
    State("upload-round-manual-tee", "value"),
    State("upload-round-manual-rating", "value"),
    State("upload-round-manual-slope", "value"),
    State("upload-round-friends-store", "data"),
    prevent_initial_call=True,
)
def handle_continue_round(
    n_clicks, course_id, tee_id, is_manual, manual_club, manual_tee,
    manual_rating, manual_slope, invited_player_ids,
):
    player_id = session.get("player_id")
    invited_player_ids = invited_player_ids or []

    if len(invited_player_ids) > 3:
        return (
            html.Span("You can only invite up to 3 friends to a round.", className="text-danger"),
            dash.no_update,
        )

    # No club_id here any more -- get_club_player_comparison works out
    # which club(s) this round counts toward itself, from whoever's
    # actually playing in it. See this page's own _upload_round_modal
    # docstring.
    payload = {
        "player_id": player_id,
        "is_manual": bool(is_manual),
        "invited_player_ids": invited_player_ids,
    }

    if is_manual:
        if not manual_club or not manual_tee:
            return (
                html.Span("Enter a club name and tee name first.", className="text-danger"),
                dash.no_update,
            )
        payload["manual_club_name"] = manual_club
        payload["manual_tee_name"] = manual_tee
        # Both optional -- rating/slope only matter for the WHS handicap
        # calculation, not for playing or scoring the round itself.
        payload["manual_course_rating"] = manual_rating
        payload["manual_slope_rating"] = manual_slope
    else:
        if not course_id or not tee_id:
            return (
                html.Span("Select a course and tees first.", className="text-danger"),
                dash.no_update,
            )
        payload["course_id"] = course_id
        payload["tee_id"] = tee_id

    with _timed("POST /rounds/"):
        response = requests.post(f"{API_BASE_URL}/rounds/", json=payload)

    if response.status_code == 201:
        # round_id explicit -- lands straight on the new round's scorecard
        # rather than back on the hub.
        return "", f"/play?round_id={response.json()['id']}"

    try:
        detail = response.json().get("detail", "Couldn't start the round.")
    except ValueError:
        detail = "Couldn't start the round."
    return html.Span(detail, className="text-danger"), dash.no_update


@callback(
    Output("upload-round-friends-store", "data", allow_duplicate=True),
    Output("upload-round-friend-picker", "value"),
    Input("upload-round-friend-picker", "value"),
    State("upload-round-friends-store", "data"),
    prevent_initial_call=True,
)
def add_friend_to_round(picked_id, selected_ids):
    # Picking a friend moves them straight into the store and snaps the
    # dropdown back to its placeholder -- selection lives in the RHS list
    # (render_friend_picker below), not as chips inside the dropdown
    # control itself. Firing again with picked_id=None (from the reset
    # below) or a friend who's somehow already selected is a no-op.
    if not picked_id:
        return dash.no_update, dash.no_update

    selected_ids = selected_ids or []
    if picked_id in selected_ids or len(selected_ids) >= 3:
        return dash.no_update, None

    return selected_ids + [picked_id], None


@callback(
    Output("upload-round-friends-store", "data", allow_duplicate=True),
    Input({"type": "upload-round-friend-remove", "player_id": ALL}, "n_clicks"),
    State("upload-round-friends-store", "data"),
    prevent_initial_call=True,
)
def remove_friend_from_round(remove_clicks, selected_ids):
    if not any(remove_clicks or []):
        # Fires once per remove button just from them being (re)rendered
        # with n_clicks=0 whenever the selection changes -- only actually
        # remove someone on a real click.
        return dash.no_update

    removed_id = dash.ctx.triggered_id["player_id"]
    return [pid for pid in (selected_ids or []) if pid != removed_id]


@callback(
    Output("upload-round-friends-selected", "children"),
    Output("upload-round-friend-picker", "options"),
    Output("upload-round-friend-picker", "disabled"),
    Output("upload-round-friend-picker", "placeholder"),
    Input("upload-round-friends-store", "data"),
    State("upload-round-friend-options-store", "data"),
)
def render_friend_picker(selected_ids, all_options):
    # No prevent_initial_call -- fires on load too, so the RHS list shows
    # its empty-state placeholder immediately instead of nothing at all.
    selected_ids = selected_ids or []
    all_options = all_options or []
    label_by_id = {opt["value"]: opt["label"] for opt in all_options}

    if selected_ids:
        selected_rows = [
            html.Div(
                className="t3g-friend-picker-chip",
                children=[
                    html.Span(label_by_id.get(pid, "Friend"), className="t3g-friend-picker-chip-name"),
                    html.Button(
                        "×",
                        id={"type": "upload-round-friend-remove", "player_id": pid},
                        className="t3g-friend-picker-chip-remove",
                        n_clicks=0,
                    ),
                ],
            )
            for pid in selected_ids
        ]
    else:
        selected_rows = [html.P("No friends added yet.", className="t3g-empty-state")]

    remaining_options = [opt for opt in all_options if opt["value"] not in selected_ids]
    at_cap = len(selected_ids) >= 3
    placeholder = "Maximum 3 friends added" if at_cap else "Add a friend..."

    return selected_rows, remaining_options, at_cap, placeholder


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
    Input("live-round-score-nr-save", "n_clicks"),
    State("live-round-active-hole-store", "data"),
    State("live-round-id-store", "data"),
    State("live-round-score-shots-store", "data"),
    State("live-round-score-putts-store", "data"),
    State("live-round-score-fairway-input", "value"),
    State("live-round-score-modal-par-store", "data"),
    State("live-round-players-store", "data"),
    prevent_initial_call=True,
)
def save_score(n_clicks, n_clicks_nr, active_hole, round_id, shots, putts, fairway_radio, par, players):
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

    # "NR" (tournament rounds only, see live-round-score-nr-save's
    # conditional rendering in components/live_scorecard.py) always saves
    # this hole as No Return regardless of whatever's currently in the
    # shots/putts steppers -- a completely separate payload from the
    # normal "Enter" save, not just a variant of it. Building the update
    # dict once, up front, and using it for both the PATCH body and the
    # in-memory players-store update below (hole.update(update)) is what
    # keeps those two always in sync -- there's no second place either
    # branch's fields could drift apart.
    if dash.ctx.triggered_id == "live-round-score-nr-save":
        update = {"strokes": None, "putts": None, "fairway_hit": None, "nr": True}
    else:
        # Par 3s don't get a fairway hit toggle in the UI -- also ignore
        # whatever's in the radio's stale value here, rather than
        # trusting a hidden control not to leak a selection into the
        # save. nr=False here is what turns a previously-NR'd hole back
        # into a real one the moment a normal score is entered again --
        # see HoleScoreUpdate.nr's docstring for why that's the intended
        # "undo", not a separate action.
        fairway_hit = None if par == 3 else _FAIRWAY_RADIO_TO_BOOL.get(fairway_radio)

        # Catching this here, before the request ever goes out, instead of
        # relying on whatever the backend happens to do with a null
        # fairway_hit -- this used to surface as a flat "Couldn't save
        # that score." with no indication of which field was the problem,
        # which is exactly what forgetting to tap Yes/No on the Fairway
        # Hit toggle produced (the field defaults to no selection, and
        # nothing else stops the Enter button from being pressed anyway).
        # Skipped for par 3s (no toggle shown at all) and for NR saves
        # (handled by the branch above, never reaches here).
        if par != 3 and fairway_hit is None:
            return _save_score_result(
                error="Select whether the fairway was hit (Yes or No) before saving this score."
            )

        update = {"strokes": shots, "putts": putts, "fairway_hit": fairway_hit, "nr": False}

    response = requests.patch(
        f"{API_BASE_URL}/rounds/{round_id}/players/{target_player_id}/holes/{hole_number}",
        json=update,
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
            hole.update(update)
            holes[str(hole_number)] = hole
            p["holes"] = holes

    # A Full Scorecard save has nothing to chain to -- the view never
    # changed to open this modal in the first place, so there's nothing
    # to navigate; just close it and land right back on the Full
    # Scorecard, exactly where the tap that opened it came from.
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
            nr = bool(hole.get("nr"))
            labels.append("NR" if nr else (str(strokes) if strokes is not None else "Enter Score"))
            classes.append(_score_marking_class(strokes, par, nr))

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
    # requesting_player_id -- who's actually tapping Finish -- is what
    # lets the backend confirm they're really an accepted player in the
    # round, now that Finish is shown to any accepted player, not just
    # the round's creator (see finish_round's docstring in backend/
    # services/rounds.py).
    response = requests.post(
        f"{API_BASE_URL}/rounds/{round_id}/finish",
        params={"requesting_player_id": session.get("player_id")},
    )

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
    # requesting_player_id lets the backend enforce "only the round's
    # creator can Scrap a casual round" -- see delete_round's docstring.
    # It's only actually checked for that one case (a casual round that's
    # still in_progress); every other use of this same endpoint (deleting
    # a finished round from Scoring History, a tournament round's Scrap)
    # ignores it, so passing it here is always safe regardless of which
    # kind of round this button is currently attached to.
    response = requests.delete(
        f"{API_BASE_URL}/rounds/{round_id}",
        params={"requesting_player_id": session.get("player_id")},
    )

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
    Output("live-round-leave-modal", "is_open"),
    Input("live-round-leave-button", "n_clicks"),
    Input("live-round-leave-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_leave_modal(open_clicks, cancel_clicks):
    return dash.ctx.triggered_id == "live-round-leave-button"


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-finish-redirect", "pathname", allow_duplicate=True),
    Output("live-round-leave-modal", "is_open", allow_duplicate=True),
    Input("live-round-leave-confirm", "n_clicks"),
    State("live-round-id-store", "data"),
    prevent_initial_call=True,
)
def confirm_leave_round(n_clicks, round_id):
    player_id = session.get("player_id")
    response = requests.post(f"{API_BASE_URL}/rounds/{round_id}/players/{player_id}/leave")

    if response.status_code in (204, 404):
        # 404 just means there's nothing left to leave (round's already
        # gone somehow) -- either way, send them home rather than showing
        # an error for a round that no longer applies to them.
        return "", "/", False

    try:
        detail = response.json().get("detail", "Couldn't leave the round.")
    except ValueError:
        detail = "Couldn't leave the round."
    return detail, dash.no_update, False


@callback(
    Output("live-round-nr-modal", "is_open"),
    Input("live-round-nr-button", "n_clicks"),
    Input("live-round-nr-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_nr_modal(open_clicks, cancel_clicks):
    return dash.ctx.triggered_id == "live-round-nr-button"


@callback(
    Output("live-round-error", "children", allow_duplicate=True),
    Output("live-round-players-store", "data", allow_duplicate=True),
    Output("live-round-nr-modal", "is_open", allow_duplicate=True),
    Input("live-round-nr-confirm", "n_clicks"),
    State("live-round-id-store", "data"),
    State("live-round-players-store", "data"),
    prevent_initial_call=True,
)
def confirm_nr_round(n_clicks, round_id, players):
    # Fills this player's own *unscored* holes with No Return -- any hole
    # that already has a real strokes value is left untouched (see mark_
    # round_no_result's docstring), the round and everyone else in it are
    # untouched either way. The players store is patched locally with the
    # same rule the backend just applied (only holes with strokes still
    # None get nr=True) rather than re-fetching the round, same pattern
    # confirm_scrap_round/confirm_leave_round use for their own updates --
    # this callback already knows exactly what changed.
    player_id = session.get("player_id")
    response = requests.post(f"{API_BASE_URL}/rounds/{round_id}/players/{player_id}/no-result")

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", "Couldn't mark No Result.")
        except ValueError:
            detail = "Couldn't mark No Result."
        return detail, dash.no_update, False

    players = [dict(p) for p in (players or [])]
    for p in players:
        if p["player_id"] == player_id:
            holes = {}
            for hole_number_str, hole in (p.get("holes") or {}).items():
                hole = dict(hole)
                if hole.get("strokes") is None:
                    hole.update({"nr": True, "strokes": None, "putts": None, "fairway_hit": None})
                holes[hole_number_str] = hole
            p["holes"] = holes

    return "", players, False


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