# target path: frontend/src/pages/home.py (full replacement)
import time
from contextlib import contextmanager

import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from flask import session

from components.scorecard import format_handicap, history_score_mark_class, live_badge, round_header_label
from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/", name="Home")

_ROUNDS_PER_PAGE = 2


@contextmanager
def _timed(label: str):
    """
    Logs how long a call to our own API took, tagged "own API" so it's
    obvious from the console which layer (frontend->backend, vs the
    backend's own external/database calls, logged separately in
    backend/services/courses.py) any slowness is actually coming from.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[TIMING] own API      {elapsed_ms:8.1f}ms  {label}")


def _course_label(course):
    # Works for local search results and freshly-imported courses -- they
    # all share these same field names.
    label = course["club_name"]
    if course.get("course_name"):
        label += f" — {course['course_name']}"
    location = course.get("county") or course.get("postcode")
    return f"{label} ({location})" if location else label


def _round_scorecard_card(round_data, player_initial, player_label):
    """Renders one completed round as a mini traditional scorecard: hole
    numbers across the top, a par row, and a scores row with the same
    birdie/bogey marks used on the live round page, plus OUT/IN/TOT/HCP/NET
    summary columns."""
    holes_by_number = {h["hole_number"]: h for h in (round_data.get("holes") or [])}
    front9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(1, 10)]
    back9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(10, 19)]

    def _sum_par(hole_subset):
        pars = [h.get("par") for h in hole_subset if h.get("par") is not None]
        return sum(pars) if pars else None

    def _sum_strokes(hole_subset):
        strokes = [h.get("strokes") for h in hole_subset if h.get("strokes") is not None]
        return sum(strokes) if strokes else None

    out_par, in_par = _sum_par(front9), _sum_par(back9)
    tot_par = out_par + in_par if out_par is not None and in_par is not None else None
    out_strokes, in_strokes = _sum_strokes(front9), _sum_strokes(back9)
    total_strokes = round_data.get("total_strokes")

    handicap = round_data.get("handicap")
    hcp_display = format_handicap(handicap)
    net_display = round(total_strokes - handicap) if (handicap is not None and total_strokes is not None) else "—"

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

    player_row = html.Tr(
        className="t3g-history-player-row",
        children=(
            [
                html.Td(
                    html.Div(
                        [
                            html.Div(player_initial, className="t3g-history-player-avatar"),
                            html.Span(player_label),
                        ],
                        className="t3g-history-player-cell",
                    )
                )
            ]
            + _score_cells(front9)
            + [html.Td(out_strokes if out_strokes is not None else "—", className="t3g-history-summary-cell")]
            + _score_cells(back9)
            + [
                html.Td(in_strokes if in_strokes is not None else "—", className="t3g-history-summary-cell"),
                html.Td(total_strokes if total_strokes is not None else "—", className="t3g-history-summary-cell"),
                html.Td(hcp_display, className="t3g-history-summary-cell"),
                html.Td(net_display, className="t3g-history-summary-cell"),
            ]
        ),
    )

    is_live = round_data.get("status") == "in_progress"
    header_children = [html.Span(round_header_label(round_data), className="t3g-round-card-title")]
    if is_live:
        header_children.append(
            html.Div(live_badge(), className="t3g-round-card-header-actions")
        )

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
                        html.Tbody([player_row]),
                    ],
                ),
            ),
        ],
    )


# Rule 5.2a exactly as USGA publishes it -- (scores on record, differentials
# used, adjustment). See backend/services/whs.py's _FEWER_THAN_20_TABLE,
# which this mirrors for display.
_HANDICAP_TABLE_ROWS = [
    ("3", "Lowest 1", "-2.0"),
    ("4", "Lowest 1", "-1.0"),
    ("5", "Lowest 1", "0"),
    ("6", "Average of lowest 2", "-1.0"),
    ("7 or 8", "Average of lowest 2", "0"),
    ("9 to 11", "Average of lowest 3", "0"),
    ("12 to 14", "Average of lowest 4", "0"),
    ("15 or 16", "Average of lowest 5", "0"),
    ("17 or 18", "Average of lowest 6", "0"),
    ("19", "Average of lowest 7", "0"),
    ("20+", "Average of lowest 8", "0"),
]

_HANDICAP_INFO_TEXT = [
    html.P(
        "Your Handicap Index is calculated automatically from your completed rounds, "
        "using the same method golf clubs worldwide use (the World Handicap System)."
    ),
    html.P(
        "Each card in \"Contributing Rounds\" packs four numbers into one shape, with "
        "no labels -- here's what they mean:"
    ),
    html.Div(
        className="t3g-handicap-annotated-example",
        children=[
            html.Div(
                className="t3g-handicap-annotation t3g-handicap-annotation--gross",
                children=["Gross score", html.Span("\u2192", className="t3g-handicap-annotation-arrow")],
            ),
            html.Div(
                className="t3g-handicap-annotation t3g-handicap-annotation--adjusted",
                children=[
                    html.Span("\u2199", className="t3g-handicap-annotation-arrow"),
                    "Adjusted score (after WHS capping)",
                ],
            ),
            html.Div(
                className="t3g-handicap-annotation t3g-handicap-annotation--slope",
                children=[html.Span("\u2196", className="t3g-handicap-annotation-arrow"), "Course slope rating"],
            ),
            html.Div(
                className="t3g-handicap-annotation t3g-handicap-annotation--date",
                children=[html.Span("\u2191", className="t3g-handicap-annotation-arrow"), "Date played"],
            ),
            html.Div(
                className="t3g-handicap-round-card t3g-handicap-round-card--counting t3g-handicap-example-card",
                children=[
                    html.Div(
                        className="t3g-handicap-round-number",
                        children=[
                            html.Span("68", className="t3g-handicap-round-gross"),
                            html.Span("68", className="t3g-handicap-round-adjusted"),
                            html.Span("131", className="t3g-handicap-round-slope"),
                        ],
                    ),
                    html.Div("2026-08-12", className="t3g-handicap-round-date"),
                ],
            ),
        ],
    ),
    html.P(
        "For each round, we work out a Score Differential -- a single number that "
        "adjusts your score for how hard the course was (its Course Rating and Slope "
        "Rating), so a round on a tough course and an easy course can be compared "
        "fairly:"
    ),
    html.Div(
        className="t3g-handicap-example",
        children=[
            html.Div(
                "Score Differential = (113 / Slope) x (Adjusted Score - Course Rating)",
                className="t3g-handicap-example-formula",
            ),
            html.Div(
                className="t3g-handicap-example-body",
                children=[
                    html.Div("Worked example", className="t3g-handicap-example-label"),
                    html.Div(
                        "You shoot 90 (your Adjusted Score, after capping any blow-up "
                        "holes) on a course with Slope 125 and Course Rating 71.2:"
                    ),
                    html.Div(
                        "(113 / 125) x (90 - 71.2) = 0.904 x 18.8 = 17.0",
                        className="t3g-handicap-example-result",
                    ),
                ],
            ),
        ],
    ),
    html.P(
        "Your Handicap Index is then the average of your best few Score Differentials "
        "from your most recent rounds (up to your last 20) -- so a couple of rough days "
        "out don't drag it up, but a great round pulls it down. Exactly how many "
        "differentials count, and any adjustment applied, depends on how many rounds "
        "you've got on record:"
    ),
    dbc.Table(
        [
            html.Thead(
                html.Tr([html.Th("Scores on record"), html.Th("Differentials used"), html.Th("Adjustment")])
            ),
            html.Tbody(
                [
                    html.Tr([html.Td(count), html.Td(used), html.Td(adj)])
                    for count, used, adj in _HANDICAP_TABLE_ROWS
                ]
            ),
        ],
        className="t3g-handicap-info-table",
        bordered=False,
        size="sm",
    ),
    html.P(
        "A few notes: a manually-entered round only counts once a Course Rating and "
        "Slope Rating are added when you start it. We don't apply the official "
        "day-to-day weather adjustment (it needs a lot of scores on the same course on "
        "the same day to work, which isn't realistic for a small group) -- everything "
        "else, including score capping and the yearly limit on how fast your handicap "
        "can rise, follows the official rules.",
        className="t3g-empty-state mt-2",
    ),
]


def _handicap_trend_figure(history):
    ordered = list(reversed(history))  # API returns most-recent-first; chart wants chronological
    dates = [h["valid_from"] for h in ordered]
    values = [h["handicap"] for h in ordered]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates,
        y=values,
        mode="lines+markers",
        line=dict(color="#c21861", width=3),
        marker=dict(color="#c21861", size=7),
        hovertemplate="%{x}<br>Handicap %{y}<extra></extra>",
    ))
    fig.update_layout(
        margin=dict(l=45, r=20, t=10, b=40),
        height=280,
        yaxis_title="Handicap Index",
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        font=dict(color="#1e2a47"),
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0f1f5", zeroline=False)
    return fig


def _handicap_trend_view(history):
    if len(history) < 2:
        return html.P(
            "Not enough handicap history yet -- play and finish a few more rounds to see a trend.",
            className="t3g-empty-state",
        )
    return dcc.Graph(figure=_handicap_trend_figure(history), config={"displayModeBar": False})


def _handicap_round_card(r):
    """One round as a compact stat card -- gross score as the big central
    number, adjusted score sitting top-right of it like an exponent,
    slope rating sitting bottom-right like a subscript, date underneath.
    Deliberately unlabeled (no "Score"/"Slope" captions) -- position
    conveys what each number is once you've seen the info modal once."""
    card_class = "t3g-handicap-round-card"
    if r["counting"]:
        card_class += " t3g-handicap-round-card--counting"

    return html.Div(
        className=card_class,
        children=[
            html.Div(
                className="t3g-handicap-round-number",
                children=[
                    html.Span(r["gross_score"], className="t3g-handicap-round-gross"),
                    html.Span(r["adjusted_gross_score"], className="t3g-handicap-round-adjusted"),
                    html.Span(r["slope_rating"], className="t3g-handicap-round-slope"),
                ],
            ),
            html.Div(r["played_on"], className="t3g-handicap-round-date"),
        ],
    )


def _handicap_rounds_view(breakdown):
    rounds = breakdown.get("rounds") or []
    if not rounds:
        return html.P(
            "No rounds counting toward your handicap yet -- rounds need a Course Rating "
            "and Slope Rating (automatic for courses in our database, optional for "
            "manually-entered rounds).",
            className="t3g-empty-state",
        )

    return html.Div(
        [
            html.Div([_handicap_round_card(r) for r in rounds], className="t3g-handicap-rounds-grid"),
            html.P(
                "Highlighted cards are currently counting toward your handicap. "
                "Top-right is your adjusted score, bottom-right is the course's slope rating.",
                className="t3g-empty-state mt-2",
            ),
        ]
    )


def _handicap_panel(current_handicap, history, breakdown):
    handicap_display = f"{current_handicap}" if current_handicap is not None else "Not set"

    return html.Div(
        className="t3g-panel",
        children=[
            html.Div(
                className="t3g-panel-navbar",
                children=[
                    html.Div(
                        [
                            html.H3("Handicap", className="t3g-panel-navbar-title"),
                            html.Span(handicap_display, className="t3g-handicap-panel-value"),
                        ],
                        className="t3g-handicap-panel-header",
                    ),
                    html.Div(
                        html.Button("i", id="handicap-info-button", className="t3g-info-button", n_clicks=0),
                        className="t3g-panel-navbar-action",
                    ),
                ],
            ),
            html.Div(
                className="t3g-panel-body",
                children=[
                    html.Div(
                        className="t3g-handicap-toggle",
                        children=[
                            html.Button(
                                "Trend",
                                id="handicap-view-trend",
                                className="t3g-handicap-toggle-button t3g-handicap-toggle-button--active",
                                n_clicks=0,
                            ),
                            html.Button(
                                "Contributing Rounds",
                                id="handicap-view-rounds",
                                className="t3g-handicap-toggle-button",
                                n_clicks=0,
                            ),
                        ],
                    ),
                    dcc.Store(id="handicap-history-store", data=history),
                    dcc.Store(id="handicap-breakdown-store", data=breakdown),
                    html.Div(id="handicap-panel-content", children=_handicap_trend_view(history)),
                ],
            ),
        ],
    )


def layout(**kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        # Not signed in (or a stale/incomplete session) — bounce to sign-in
        session.clear()
        return dcc.Location(pathname="/signin", id="redirect-to-signin", refresh=True)

    with _timed(f"GET /club-players/player/{player_id}"):
        clubs_resp = requests.get(f"{API_BASE_URL}/club-players/player/{player_id}")
    clubs = (
        [row["clubs"] for row in clubs_resp.json()]
        if clubs_resp.status_code == 200
        else []
    )

    if clubs:
        clubs_section = html.Div(
            className="t3g-clubs-list",
            children=[
                dcc.Link(
                    club["name"],
                    href=f"/clubs/{club['slug']}",
                    className="t3g-club-item",
                )
                for club in clubs
            ],
        )
    else:
        clubs_section = html.P(
            "You're not in any clubs yet.", className="t3g-empty-state"
        )

    # Preload every cached club (a few hundred rows -- cheap) so the round
    # course picker can filter client-side, same approach as My Account's
    # home-course field. Value here is the internal course id (not just a
    # display string), since we need it to look up/import tees.
    with _timed("GET /courses/"):
        courses_resp = requests.get(f"{API_BASE_URL}/courses/")
    courses = courses_resp.json() if courses_resp.status_code == 200 else []
    course_options = [{"label": _course_label(c), "value": c["id"]} for c in courses]

    with _timed(f"GET /friends/player/{player_id}"):
        friends_resp = requests.get(f"{API_BASE_URL}/friends/player/{player_id}")
    friends = friends_resp.json() if friends_resp.status_code == 200 else []
    friend_invite_options = [
        {"label": f.get("nickname") or f"{f.get('first_name', '')} {f.get('surname', '')}".strip(), "value": f["player_id"]}
        for f in friends
    ]

    with _timed(f"GET /rounds/invites/{player_id}"):
        round_invites_resp = requests.get(f"{API_BASE_URL}/rounds/invites/{player_id}")
    round_invites = round_invites_resp.json() if round_invites_resp.status_code == 200 else []

    with _timed(f"GET /club-invites/player/{player_id}"):
        club_invites_resp = requests.get(f"{API_BASE_URL}/club-invites/player/{player_id}")
    club_invites = club_invites_resp.json() if club_invites_resp.status_code == 200 else []

    with _timed(f"GET /handicaps/player/{player_id}/current"):
        current_handicap_resp = requests.get(f"{API_BASE_URL}/handicaps/player/{player_id}/current")
    current_handicap = (
        current_handicap_resp.json().get("handicap") if current_handicap_resp.status_code == 200 else None
    )

    with _timed(f"GET /handicaps/player/{player_id}"):
        handicap_history_resp = requests.get(f"{API_BASE_URL}/handicaps/player/{player_id}")
    handicap_history = handicap_history_resp.json() if handicap_history_resp.status_code == 200 else []

    with _timed(f"GET /handicaps/player/{player_id}/breakdown"):
        handicap_breakdown_resp = requests.get(f"{API_BASE_URL}/handicaps/player/{player_id}/breakdown")
    handicap_breakdown = (
        handicap_breakdown_resp.json()
        if handicap_breakdown_resp.status_code == 200
        else {"handicap_index": None, "rounds": []}
    )

    with _timed(f"GET /rounds/player/{player_id}"):
        rounds_resp = requests.get(f"{API_BASE_URL}/rounds/player/{player_id}")
    rounds_history = rounds_resp.json() if rounds_resp.status_code == 200 else []

    # Live rounds no longer show in Rounds History at all -- they get
    # their own panel up top instead (built below, alongside
    # invites_section), the same treatment as a round invite,
    # since "a round that's happening right now" is a different kind of
    # thing from "a round that already happened".
    live_round = next((r for r in rounds_history if r.get("status") == "in_progress"), None)
    completed_rounds = [r for r in rounds_history if r.get("status") != "in_progress"]

    # Only needed for the scorecard's avatar/name -- skip the call
    # entirely when there's nothing (live or completed) to render one for.
    player_info = {"initial": "Y", "label": "You"}
    if rounds_history:
        with _timed(f"GET /players/{player_id}"):
            player_resp = requests.get(f"{API_BASE_URL}/players/{player_id}")
        player = player_resp.json() if player_resp.status_code == 200 else {}
        player_label = player.get("nickname") or player.get("first_name") or "You"
        player_initial = player_label[0].upper() if player_label else "Y"
        player_info = {"initial": player_initial, "label": player_label}

    live_round_section = None
    if live_round:
        live_round_section = html.Div(
            className="t3g-panel",
            children=[
                build_panel_navbar(
                    "Live Round",
                    action=dcc.Link(
                        "Continue Round",
                        href="/live-round",
                        className="t3g-panel-action-button",
                        style={"textDecoration": "none"},
                    ),
                ),
                html.Div(
                    _round_scorecard_card(live_round, player_info["initial"], player_info["label"]),
                    className="t3g-panel-body",
                ),
            ],
        )

    # The Stores and pagination controls always render, even with zero
    # completed rounds -- change_rounds_page/render_rounds_page target
    # these ids unconditionally, and Dash throws a runtime "nonexistent
    # object... in an Output" error if they're ever left out of the DOM
    # entirely rather than just having nothing to page through.
    # render_rounds_page swaps in a "no rounds" placeholder itself.
    rounds_section = html.Div(
        children=[
            dcc.Store(id="home-rounds-store", data=completed_rounds),
            dcc.Store(id="home-rounds-player-store", data=player_info),
            dcc.Store(id="home-rounds-page", data=0),
            html.Div(id="home-rounds-page-list", className="t3g-rounds-list"),
            html.Div(
                className="t3g-rounds-pagination",
                children=[
                    html.Button("‹ Newer", id="home-rounds-prev", n_clicks=0, className="t3g-pagination-button"),
                    html.Span(id="home-rounds-page-label", className="t3g-pagination-label"),
                    html.Button("Older ›", id="home-rounds-next", n_clicks=0, className="t3g-pagination-button"),
                ],
            ),
        ],
    )

    # Round invites and club invites used to be two separate panels --
    # bucketed into one "Invites" panel now, round invites first then club
    # invites. Both error placeholders always render together (rather than
    # only the one matching whichever list is non-empty) so accepting/
    # declining either type never targets a Dash Output that isn't in the
    # DOM -- same defensive pattern as the rounds-history pagination fix.
    invites_section = None
    if round_invites or club_invites:
        round_invite_rows = [
            html.Div(
                className="t3g-friend-request-row",
                children=[
                    html.Span(
                        f"{invite.get('owner_first_name', '')} {invite.get('owner_surname', '')} "
                        f"invited you to a round"
                        + (f" at {invite['club_name']}" if invite.get("club_name") else ""),
                        className="t3g-friend-request-name",
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Accept",
                                id={"type": "round-invite-accept", "round_id": invite["round_id"]},
                                className="t3g-panel-action-button",
                                n_clicks=0,
                            ),
                            html.Button(
                                "Decline",
                                id={"type": "round-invite-decline", "round_id": invite["round_id"]},
                                className="t3g-panel-action-button t3g-panel-action-button--secondary",
                                n_clicks=0,
                            ),
                        ],
                        className="t3g-friend-request-actions",
                    ),
                ],
            )
            for invite in round_invites
        ]

        club_invite_rows = [
            html.Div(
                className="t3g-friend-request-row",
                children=[
                    html.Span(
                        f"{invite.get('inviter', {}).get('first_name', '')} "
                        f"{invite.get('inviter', {}).get('surname', '')} invited you to join "
                        f"{invite.get('clubs', {}).get('name', 'a club')}",
                        className="t3g-friend-request-name",
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Accept",
                                id={"type": "club-invite-accept", "invite_id": invite["id"]},
                                className="t3g-panel-action-button",
                                n_clicks=0,
                            ),
                            html.Button(
                                "Decline",
                                id={"type": "club-invite-decline", "invite_id": invite["id"]},
                                className="t3g-panel-action-button t3g-panel-action-button--secondary",
                                n_clicks=0,
                            ),
                        ],
                        className="t3g-friend-request-actions",
                    ),
                ],
            )
            for invite in club_invites
        ]

        invites_section = html.Div(
            className="t3g-panel",
            children=[
                build_panel_navbar("Invites"),
                html.Div(
                    className="t3g-panel-body",
                    children=[
                        html.Div(id="round-invite-error", className="text-danger mb-2"),
                        html.Div(id="club-invite-error", className="text-danger mb-2"),
                        html.Div(
                            className="t3g-friend-request-list",
                            children=round_invite_rows + club_invite_rows,
                        ),
                    ],
                ),
            ],
        )

    return html.Div(
        className="t3g-page",
        children=[
            dcc.Location(id="round-invite-refresh", refresh=True),
            dcc.Location(id="club-invite-refresh", refresh=True),
            live_round_section,
            invites_section,
            _handicap_panel(current_handicap, handicap_history, handicap_breakdown),
            html.Div(
                className="t3g-panel-grid",
                children=[
                    html.Div(
                        className="t3g-panel",
                        children=[
                            build_panel_navbar(
                                "Your Clubs",
                                action=html.Button(
                                    "Create Club",
                                    id="create-club-button",
                                    className="t3g-panel-action-button",
                                ),
                            ),
                            html.Div(clubs_section, className="t3g-panel-body"),
                        ],
                    ),
                    html.Div(
                        className="t3g-panel",
                        children=[
                            build_panel_navbar(
                                "Rounds History",
                                action=html.Button(
                                    "Upload New Round",
                                    id="upload-round-button",
                                    className="t3g-panel-action-button",
                                ),
                            ),
                            html.Div(rounds_section, className="t3g-panel-body"),
                        ],
                    ),
                ],
            ),
            dbc.Modal(
                id="create-club-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Create a Club")),
                    dbc.ModalBody(
                        [
                            dbc.Input(
                                id="create-club-name-input",
                                placeholder="Club name",
                                type="text",
                                className="mb-2",
                            ),
                            dbc.Textarea(
                                id="create-club-description-input",
                                placeholder="Description (optional)",
                            ),
                            html.Div(
                                id="create-club-error", className="text-danger mt-2"
                            ),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button(
                                "Cancel", id="create-club-cancel", color="secondary"
                            ),
                            dbc.Button(
                                "Create", id="create-club-submit", color="primary"
                            ),
                        ]
                    ),
                ],
            ),
            dcc.Location(id="create-club-redirect", refresh=True),
            dbc.Modal(
                id="upload-round-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Upload New Round")),
                    dbc.ModalBody(
                        [
                            dcc.Dropdown(
                                id="upload-round-course",
                                placeholder="Search for the course you played",
                                options=course_options,
                                searchable=True,
                                clearable=True,
                                className="mb-2 t3g-course-dropdown",
                            ),
                            dcc.Loading(
                                type="circle",
                                children=dcc.Dropdown(
                                    id="upload-round-tee",
                                    placeholder="Select tees",
                                    options=[],
                                    disabled=True,
                                    className="mb-1 t3g-course-dropdown",
                                ),
                            ),
                            html.Div(id="upload-round-tee-status", className="t3g-empty-state mt-1"),
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
                            else None,
                            # Always rendered (even with empty options) so
                            # the State lookup below never points at a
                            # missing component.
                            dcc.Checklist(
                                id="upload-round-friends",
                                options=friend_invite_options,
                                value=[],
                                className="t3g-friend-invite-checklist",
                            ),
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
            dbc.Modal(
                id="handicap-info-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("How your handicap is calculated")),
                    dbc.ModalBody(_HANDICAP_INFO_TEXT),
                    dbc.ModalFooter(dbc.Button("Got it", id="handicap-info-close", color="primary")),
                ],
            ),
        ],
    )


@callback(
    Output("create-club-modal", "is_open"),
    Output("create-club-error", "children"),
    Output("create-club-redirect", "pathname"),
    Input("create-club-button", "n_clicks"),
    Input("create-club-cancel", "n_clicks"),
    Input("create-club-submit", "n_clicks"),
    State("create-club-name-input", "value"),
    State("create-club-description-input", "value"),
    prevent_initial_call=True,
)
def handle_create_club(open_clicks, cancel_clicks, submit_clicks, name, description):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "create-club-button":
        return True, "", dash.no_update

    if triggered_id == "create-club-cancel":
        return False, "", dash.no_update

    if triggered_id == "create-club-submit":
        if not name:
            return True, "Enter a club name.", dash.no_update

        player_id = session.get("player_id")
        with _timed("POST /clubs/"):
            club_resp = requests.post(
                f"{API_BASE_URL}/clubs/",
                json={
                    "name": name,
                    "description": description or None,
                    "admin_player_id": player_id,
                },
            )

        if club_resp.status_code == 409:
            return (
                True,
                "A club with a similar name already exists. Try a different name.",
                dash.no_update,
            )
        if club_resp.status_code != 201:
            return True, "Couldn't create the club. Try again.", dash.no_update

        new_club = club_resp.json()

        # Automatically add the creator as a member too, so the club shows
        # up in their own clubs list right away. Best-effort: if this call
        # fails, the club still exists and can be joined manually by ID.
        with _timed("POST /club-players/ (auto-join creator)"):
            requests.post(
                f"{API_BASE_URL}/club-players/",
                json={"club_id": new_club["id"], "player_id": player_id},
            )

        return False, "", "/"

    return dash.no_update, dash.no_update, dash.no_update


@callback(
    Output("upload-round-modal", "is_open"),
    Output("upload-round-course", "value"),
    Output("upload-round-redirect", "pathname", allow_duplicate=True),
    Output("upload-round-manual-mode", "data", allow_duplicate=True),
    Output("upload-round-manual-fields", "style", allow_duplicate=True),
    Output("upload-round-course", "style", allow_duplicate=True),
    Output("upload-round-tee-status", "style", allow_duplicate=True),
    Input("upload-round-button", "n_clicks"),
    Input("upload-round-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_upload_round_modal(open_clicks, cancel_clicks):
    # Resetting the course value on open (via the Output below) also
    # triggers load_tees_for_course(None), which clears the tee dropdown --
    # so the modal always starts fresh (manual mode off too) rather than
    # showing a stale selection from last time it was opened.
    triggered_id = dash.ctx.triggered_id
    reset_manual = (False, {"display": "none"}, {}, {})

    if triggered_id == "upload-round-button":
        player_id = session.get("player_id")
        with _timed(f"GET /rounds/active/{player_id}"):
            response = requests.get(f"{API_BASE_URL}/rounds/active/{player_id}")

        if response.status_code == 200:
            # Already have a live round in progress -- go straight there
            # instead of opening the modal. The backend would reject a
            # second one anyway (one-active-round-per-player), but this
            # avoids making them fill out the form just to be told no.
            return (False, dash.no_update, "/live-round", *reset_manual)

        return (True, None, dash.no_update, *reset_manual)

    return (False, dash.no_update, dash.no_update, *reset_manual)


@callback(
    Output("upload-round-manual-fields", "style", allow_duplicate=True),
    Output("upload-round-course", "style", allow_duplicate=True),
    Output("upload-round-tee-status", "style", allow_duplicate=True),
    Output("upload-round-manual-mode", "data", allow_duplicate=True),
    Input("upload-round-manual-toggle", "n_clicks"),
    State("upload-round-manual-mode", "data"),
    prevent_initial_call=True,
)
def toggle_manual_entry(n_clicks, is_manual):
    is_manual = not bool(is_manual)

    if is_manual:
        return {"display": "block"}, {"display": "none"}, {"display": "none"}, True

    return {"display": "none"}, {}, {}, False


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
    State("upload-round-friends", "value"),
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
        return "", "/live-round"

    try:
        detail = response.json().get("detail", "Couldn't start the round.")
    except ValueError:
        detail = "Couldn't start the round."
    return html.Span(detail, className="text-danger"), dash.no_update


@callback(
    Output("home-rounds-page", "data"),
    Input("home-rounds-prev", "n_clicks"),
    Input("home-rounds-next", "n_clicks"),
    State("home-rounds-page", "data"),
    State("home-rounds-store", "data"),
    prevent_initial_call=True,
)
def change_rounds_page(prev_clicks, next_clicks, page, completed_rounds):
    triggered_id = dash.ctx.triggered_id
    page = page or 0
    max_page = max(0, (len(completed_rounds or []) - 1) // _ROUNDS_PER_PAGE)

    if triggered_id == "home-rounds-prev":
        return max(0, page - 1)
    if triggered_id == "home-rounds-next":
        return min(max_page, page + 1)
    return page


@callback(
    Output("home-rounds-page-list", "children"),
    Output("home-rounds-prev", "disabled"),
    Output("home-rounds-next", "disabled"),
    Output("home-rounds-page-label", "children"),
    Input("home-rounds-page", "data"),
    State("home-rounds-store", "data"),
    State("home-rounds-player-store", "data"),
)
def render_rounds_page(page, completed_rounds, player_info):
    # Fires on load too (no prevent_initial_call) so the first page renders
    # immediately rather than waiting on a button click.
    completed_rounds = completed_rounds or []
    player_info = player_info or {}
    page = page or 0

    if not completed_rounds:
        placeholder = html.P("No rounds to show yet.", className="t3g-empty-state")
        return placeholder, True, True, ""

    total_pages = max(1, -(-len(completed_rounds) // _ROUNDS_PER_PAGE))  # ceil div
    start = page * _ROUNDS_PER_PAGE
    page_rounds = completed_rounds[start:start + _ROUNDS_PER_PAGE]

    cards = [
        _round_scorecard_card(r, player_info.get("initial", "Y"), player_info.get("label", "You"))
        for r in page_rounds
    ]

    label = f"{page + 1} of {total_pages}"
    return cards, page <= 0, page >= total_pages - 1, label


@callback(
    Output("round-invite-refresh", "pathname"),
    Output("round-invite-error", "children"),
    Input({"type": "round-invite-accept", "round_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def accept_round_invite(n_clicks_list):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    player_id = session.get("player_id")
    with _timed(f"POST /rounds/{triggered_id['round_id']}/invites/{player_id}/accept"):
        response = requests.post(
            f"{API_BASE_URL}/rounds/{triggered_id['round_id']}/invites/{player_id}/accept"
        )

    if response.status_code == 200:
        return "/live-round", ""

    try:
        detail = response.json().get("detail", "Couldn't accept that invite.")
    except ValueError:
        detail = "Couldn't accept that invite."
    return dash.no_update, detail


@callback(
    Output("round-invite-refresh", "pathname", allow_duplicate=True),
    Input({"type": "round-invite-decline", "round_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def decline_round_invite(n_clicks_list):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(n_clicks_list):
        return dash.no_update

    player_id = session.get("player_id")
    with _timed(f"POST /rounds/{triggered_id['round_id']}/invites/{player_id}/decline"):
        requests.post(f"{API_BASE_URL}/rounds/{triggered_id['round_id']}/invites/{player_id}/decline")

    return "/"

@callback(
    Output("handicap-panel-content", "children"),
    Output("handicap-view-trend", "className"),
    Output("handicap-view-rounds", "className"),
    Input("handicap-view-trend", "n_clicks"),
    Input("handicap-view-rounds", "n_clicks"),
    State("handicap-history-store", "data"),
    State("handicap-breakdown-store", "data"),
    prevent_initial_call=True,
)
def render_handicap_view(trend_clicks, rounds_clicks, history, breakdown):
    triggered_id = dash.ctx.triggered_id
    base_class = "t3g-handicap-toggle-button"
    active_class = f"{base_class} t3g-handicap-toggle-button--active"

    if triggered_id == "handicap-view-rounds":
        return _handicap_rounds_view(breakdown or {}), base_class, active_class

    return _handicap_trend_view(history or []), active_class, base_class


@callback(
    Output("handicap-info-modal", "is_open"),
    Input("handicap-info-button", "n_clicks"),
    Input("handicap-info-close", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_handicap_info_modal(open_clicks, close_clicks):
    return dash.ctx.triggered_id == "handicap-info-button"


@callback(
    Output("club-invite-refresh", "pathname"),
    Output("club-invite-error", "children"),
    Input({"type": "club-invite-accept", "invite_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def accept_club_invite(n_clicks_list):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    player_id = session.get("player_id")
    with _timed(f"POST /club-invites/{triggered_id['invite_id']}/accept"):
        response = requests.post(
            f"{API_BASE_URL}/club-invites/{triggered_id['invite_id']}/accept",
            params={"player_id": player_id},
        )

    if response.status_code == 200:
        return "/", ""

    try:
        detail = response.json().get("detail", "Couldn't accept that invite.")
    except ValueError:
        detail = "Couldn't accept that invite."
    return dash.no_update, detail


@callback(
    Output("club-invite-refresh", "pathname", allow_duplicate=True),
    Input({"type": "club-invite-decline", "invite_id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def decline_club_invite(n_clicks_list):
    triggered_id = dash.ctx.triggered_id
    if not triggered_id or not any(n_clicks_list):
        return dash.no_update

    player_id = session.get("player_id")
    with _timed(f"POST /club-invites/{triggered_id['invite_id']}/decline"):
        requests.post(
            f"{API_BASE_URL}/club-invites/{triggered_id['invite_id']}/decline",
            params={"player_id": player_id},
        )

    return "/"