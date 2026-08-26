# target path: frontend/src/pages/my_profile.py (new file)
"""
The "My Profile" tab under My Account -- everything that used to be the
Home page's own content (pending invites, Handicap Index panel, Your
Clubs grid, Create Club modal) before Home became the activity feed.
Moved here wholesale rather than cross-imported from home.py -- same
"small per-page copies, fully self-contained ids/callbacks" convention
profile.py's own docstring already explains for its own duplicated
_round_scorecard_card/_handicap_* helpers, just applied to the entire
page this time instead of a handful of functions.

Live rounds and Start New Round (originally part of this same move) have
since moved on again, to the new Play page -- see pages/play.py's own
module docstring. This page's Rounds History panel only ever shows
*completed* rounds now; "what am I playing right now" and "how do I
start a round" both live on Play instead.
"""
import time
from contextlib import contextmanager

import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from flask import session

from components.scorecard import (
    format_handicap,
    history_score_mark_class,
    live_badge,
    round_header_label,
    tournament_round_badge,
)
from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/my-account/profile", name="My Profile")

# Tournament-style pill subnav shared with my_account.py/friends.py --
# each of the three keeps its own copy rather than cross-importing (same
# convention noted above), so this needs its own _ACCOUNT_TAB_BASE/
# _ACCOUNT_TAB_ACTIVE + _account_subnav rather than reusing my_account.py's.
_ACCOUNT_TAB_BASE = "t3g-tournament-tab"
_ACCOUNT_TAB_ACTIVE = "t3g-tournament-tab t3g-tournament-tab--active"


def _account_subnav(active):
    return html.Div(
        className="t3g-tournament-subnav",
        children=html.Div(
            className="t3g-tournament-subnav-inner",
            children=html.Div(
                className="t3g-tournament-tabs",
                children=[
                    dcc.Link(
                        "My Account",
                        href="/my-account",
                        className=_ACCOUNT_TAB_ACTIVE if active == "account" else _ACCOUNT_TAB_BASE,
                    ),
                    dcc.Link(
                        "My Profile",
                        href="/my-account/profile",
                        className=_ACCOUNT_TAB_ACTIVE if active == "profile" else _ACCOUNT_TAB_BASE,
                    ),
                    dcc.Link(
                        "Friends",
                        href="/friends",
                        className=_ACCOUNT_TAB_ACTIVE if active == "friends" else _ACCOUNT_TAB_BASE,
                    ),
                ],
            ),
        ),
    )


_ROUNDS_PER_PAGE = 2


def _club_initials(name):
    """Fallback tile content for a club with no photo uploaded yet -- up to
    the first two words' initials (e.g. "Senco Squad" -> "SS", "Ashford" ->
    "A"), same idea as a lot of avatar-placeholder patterns elsewhere."""
    words = (name or "").split()
    initials = "".join(w[0] for w in words[:2] if w)
    return initials.upper() or "?"


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


def _round_scorecard_card(round_data, player_rows):
    """Renders one round as a mini traditional scorecard: hole numbers
    across the top, a par row, and one player row per entry in
    player_rows (a single row for a solo/completed round, one row per
    participant for a live round with other people in it), with the same
    birdie/bogey marks used on the live round page, plus OUT/IN/TOT/HCP/NET
    summary columns per row.

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

    # Tournament badge shows regardless of live/completed status (a
    # finished tournament round should still read as one), live_badge
    # only while it's actually in progress -- both can show together,
    # since round_header_label's own tournament-aware title text is only
    # the first layer of the "is this a tournament round" distinction.
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

# Static illustrative diagram for the handicap info modal -- a worked
# example "GROSS SCORE" card (not tied to any real round) with a callout
# bubble + short explanation for each of the four numbers a real
# Contributing Rounds card packs together unlabeled. Built as one inline
# SVG (rendered below via dcc.Markdown(dangerously_allow_html=True))
# rather than as nested html.Div/CSS like the rest of this app's UI --
# the diagonal dotted connector lines from each bubble to its exact spot
# on the card are what actually make the annotations legible, and that's
# far simpler to get pixel-accurate as one static drawing than to fake
# with CSS borders/pseudo-elements. Every number and date here is a fixed
# example, same as the old annotated diagram it replaces.
_HANDICAP_DIAGRAM_SVG = """
<svg viewBox="0 0 720 560" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Example Gross Score card, annotated: gross score, adjusted score after WHS capping, course slope rating, and date played">
<line x1="168" y1="245" x2="240" y2="245" stroke="#c21861" stroke-width="2" stroke-dasharray="4 5"/>
<line x1="564" y1="160" x2="480" y2="195" stroke="#c21861" stroke-width="2" stroke-dasharray="4 5"/>
<line x1="563" y1="323" x2="480" y2="282" stroke="#c21861" stroke-width="2" stroke-dasharray="4 5"/>
<line x1="360" y1="392" x2="360" y2="432" stroke="#c21861" stroke-width="2" stroke-dasharray="4 5"/>
<rect x="240" y="92" width="240" height="300" rx="18" fill="#fdf5f9" stroke="#c21861" stroke-width="2"/>
<rect x="270" y="72" width="180" height="40" rx="20" fill="#c21861"/>
<text x="360" y="98" text-anchor="middle" font-size="15" font-weight="700" fill="#ffffff" letter-spacing="1" font-family="inherit">GROSS SCORE</text>
<text x="300" y="270" text-anchor="middle" font-size="76" font-weight="800" fill="#1e2a47" font-family="inherit">68</text>
<line x1="360" y1="140" x2="360" y2="320" stroke="#e2e4ea" stroke-width="1.5"/>
<text x="378" y="195" font-size="26" font-weight="800" fill="#c21861" font-family="inherit">68</text>
<text x="378" y="215" font-size="11" font-weight="700" fill="#6b7280" font-family="inherit">After</text>
<text x="378" y="228" font-size="11" font-weight="700" fill="#6b7280" font-family="inherit">WHS capping</text>
<line x1="378" y1="242" x2="465" y2="242" stroke="#e2e4ea" stroke-width="1.5"/>
<text x="378" y="272" font-size="26" font-weight="800" fill="#c21861" font-family="inherit">131</text>
<text x="378" y="292" font-size="11" font-weight="700" fill="#6b7280" font-family="inherit">Before</text>
<text x="378" y="305" font-size="11" font-weight="700" fill="#6b7280" font-family="inherit">WHS capping</text>
<line x1="240" y1="330" x2="480" y2="330" stroke="#e2e4ea" stroke-width="1.5"/>
<g transform="translate(258,344)">
<rect x="0" y="2" width="20" height="18" rx="3" fill="none" stroke="#c21861" stroke-width="2"/>
<line x1="0" y1="9" x2="20" y2="9" stroke="#c21861" stroke-width="2"/>
<line x1="5" y1="0" x2="5" y2="5" stroke="#c21861" stroke-width="2"/>
<line x1="15" y1="0" x2="15" y2="5" stroke="#c21861" stroke-width="2"/>
</g>
<text x="288" y="360" font-size="16" font-weight="800" fill="#1e2a47" font-family="inherit">2026-08-12</text>
<text x="288" y="376" font-size="10" font-weight="700" fill="#6b7280" letter-spacing="1" font-family="inherit">DATE PLAYED</text>
<circle cx="138" cy="245" r="30" fill="#fbe6f0"/>
<g transform="translate(138,245)" stroke="#c21861" stroke-width="2" fill="none">
<circle r="11"/>
<circle r="5.5"/>
<circle r="1.2" fill="#c21861" stroke="none"/>
</g>
<text x="138" y="297" text-anchor="middle" font-size="16" font-weight="800" fill="#1e2a47" font-family="inherit">Gross score</text>
<text x="138" y="317" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">The final score</text>
<text x="138" y="333" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">for the round</text>
<text x="138" y="349" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">after WHS capping.</text>
<circle cx="592" cy="150" r="30" fill="#fbe6f0"/>
<g transform="translate(592,150)" fill="#c21861">
<rect x="-11" y="2" width="6" height="9"/>
<rect x="-2" y="-4" width="6" height="15"/>
<rect x="7" y="-9" width="6" height="20"/>
</g>
<text x="592" y="200" text-anchor="middle" font-size="16" font-weight="800" fill="#1e2a47" font-family="inherit">Adjusted score</text>
<text x="592" y="218" text-anchor="middle" font-size="13" font-weight="800" fill="#1e2a47" font-family="inherit">(after WHS capping)</text>
<text x="592" y="238" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">Your score after</text>
<text x="592" y="254" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">World Handicap System</text>
<text x="592" y="270" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">capping has been applied.</text>
<circle cx="592" cy="330" r="30" fill="#fbe6f0"/>
<g transform="translate(592,330)" stroke="#c21861" stroke-width="2.5" fill="none" stroke-linecap="round">
<path d="M -13 6 A 15 15 0 0 1 13 6"/>
<line x1="0" y1="6" x2="7" y2="-6"/>
<circle cx="0" cy="6" r="2" fill="#c21861" stroke="none"/>
</g>
<text x="592" y="382" text-anchor="middle" font-size="16" font-weight="800" fill="#1e2a47" font-family="inherit">Course slope rating</text>
<text x="592" y="402" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">The difficulty rating of</text>
<text x="592" y="418" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">the course for the tees</text>
<text x="592" y="434" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">played.</text>
<circle cx="360" cy="462" r="30" fill="#fbe6f0"/>
<g transform="translate(360,462)">
<rect x="-11" y="-8" width="22" height="19" rx="3" fill="none" stroke="#c21861" stroke-width="2"/>
<line x1="-11" y1="-1" x2="11" y2="-1" stroke="#c21861" stroke-width="2"/>
<line x1="-6" y1="-11" x2="-6" y2="-6" stroke="#c21861" stroke-width="2"/>
<line x1="6" y1="-11" x2="6" y2="-6" stroke="#c21861" stroke-width="2"/>
</g>
<text x="360" y="514" text-anchor="middle" font-size="16" font-weight="800" fill="#1e2a47" font-family="inherit">Date played</text>
<text x="360" y="534" text-anchor="middle" font-size="12" fill="#6b7280" font-family="inherit">The date when the round was played.</text>
</svg>
"""

_HANDICAP_INFO_TEXT = [
    html.P(
        "Your Handicap Index is calculated automatically from your completed rounds, "
        "using the same method golf clubs worldwide use (the World Handicap System)."
    ),
    html.P(
        "Each card in \"Contributing Rounds\" packs four numbers into one shape, with "
        "no labels -- here's what they mean:"
    ),
    dcc.Markdown(
        _HANDICAP_DIAGRAM_SVG,
        dangerously_allow_html=True,
        className="t3g-handicap-diagram-markdown",
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


# Every dcc.Graph in this module passes this exact dict -- responsive:
# True is what lets Plotly resize the chart to fit its container's actual
# width instead of rendering at a fixed ~700px (see analysis.py's
# GRAPH_CONFIG for the full explanation; carried over here for the same
# reason, and for visual consistency across every trend chart in the app).
GRAPH_CONFIG = {"displayModeBar": False, "responsive": True}


def _padded_range(values, factor=1.5):
    """Same helper as analysis.py's _padded_range -- a y-axis range
    `factor` times as wide as the data's own min-to-max span, centered on
    the data, so the handicap trend line isn't flattened into a thin band
    at the top of a zero-anchored chart."""
    if not values:
        return None
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        pad = max(abs(hi) * 0.15, 1)
        return [lo - pad, hi + pad]
    extra = (span * factor - span) / 2
    return [lo - extra, hi + extra]


def _handicap_trend_figure(history):
    ordered = list(reversed(history))  # API returns most-recent-first; chart wants chronological
    dates = [h["valid_from"] for h in ordered]
    values = [h["handicap"] for h in ordered]

    fig = go.Figure()
    # Smoothed spline + a soft gradient fill under the line, rather than a
    # bare connect-the-dots polyline -- same "real analytics chart" look
    # as the Player Analysis trend charts (see analysis.py's _build_
    # figure), applied here too so every trend chart in the app reads as
    # one consistent visual language. White-ringed ("halo") marker so
    # each point still reads clearly against its own gradient fill.
    fig.add_trace(go.Scatter(
        x=dates,
        y=values,
        mode="lines+markers",
        line=dict(color="#c21861", width=2, shape="spline", smoothing=0.6),
        marker=dict(color="#c21861", size=6, line=dict(color="#ffffff", width=1.5)),
        hovertemplate="%{x}<br>Handicap %{y}<extra></extra>",
    ))
    fig.update_layout(
        autosize=True,
        # No yaxis_title -- the panel's own navbar already says "Handicap",
        # and dropping the rotated axis label frees up the left margin
        # (below) for the plot itself, same fix as analysis.py's charts.
        margin=dict(l=8, r=40, t=28, b=28),
        height=280,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#1e2a47"),
        showlegend=False,
        hoverlabel=dict(bgcolor="#1e2a47", bordercolor="#1e2a47", font=dict(color="#ffffff")),
    )
    fig.update_xaxes(
        showgrid=False, zeroline=False, tickformat="%b %d", nticks=4, tickfont=dict(size=10, color="#9aa0b0")
    )
    fig.update_yaxes(
        side="right",
        showgrid=True,
        gridcolor="#e7e9f0",
        griddash="dash",
        gridwidth=1,
        zeroline=False,
        range=_padded_range(values),
        nticks=4,
        tickfont=dict(size=10, color="#9aa0b0"),
    )
    if values:
        # Solid pill "current value" tag, matching analysis.py's trend
        # chart badges, instead of plain floating text.
        fig.add_annotation(
            x=dates[-1],
            y=values[-1],
            text=f"<b>{values[-1]}</b>",
            showarrow=False,
            xanchor="center",
            yshift=20,  # floats the badge above the point instead of off to its side
            font=dict(color="#ffffff", size=11),
            bgcolor="#c21861",
            bordercolor="#c21861",
            borderpad=4,
        )
    return fig


def _handicap_trend_view(history):
    if len(history) < 2:
        return html.P(
            "Not enough handicap history yet -- play and finish a few more rounds to see a trend.",
            className="t3g-empty-state",
        )
    return dcc.Graph(
        figure=_handicap_trend_figure(history),
        config=GRAPH_CONFIG,
        # Fixed pixel height, not just width -- config.responsive=True
        # re-measures its container for *both* dimensions on every resize.
        # An "auto" height here (derived from the chart's own rendered
        # content) turns that into a feedback loop: draw -> box grows to
        # fit -> resize observer fires -> draw taller -> repeat, with no
        # ceiling. See analysis.py's _build_analysis_body for the full note.
        style={"width": "100%", "height": "280px"},
    )


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
                                className="t3g-handicap-toggle-button",
                                n_clicks=0,
                            ),
                            html.Button(
                                "Contributing Rounds",
                                id="handicap-view-rounds",
                                # Contributing Rounds is the default view now
                                # (was Trend) -- the initial content below is
                                # built from _handicap_rounds_view to match.
                                className="t3g-handicap-toggle-button t3g-handicap-toggle-button--active",
                                n_clicks=0,
                            ),
                        ],
                    ),
                    dcc.Store(id="handicap-history-store", data=history),
                    dcc.Store(id="handicap-breakdown-store", data=breakdown),
                    html.Div(id="handicap-panel-content", children=_handicap_rounds_view(breakdown)),
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
                    href=f"/clubs/{club['slug']}",
                    className="t3g-club-item",
                    children=[
                        (
                            html.Img(src=club["photo_url"], className="t3g-club-item-photo")
                            if club.get("photo_url")
                            else html.Div(
                                _club_initials(club.get("name")),
                                className="t3g-club-item-photo-placeholder",
                            )
                        ),
                        html.Div(club["name"], className="t3g-club-item-name"),
                    ],
                )
                for club in clubs
            ],
        )
    else:
        clubs_section = html.P(
            "You're not in any clubs yet.", className="t3g-empty-state"
        )

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

    # Live rounds (casual and tournament) no longer show here at all --
    # they, and Start New Round, both moved to the Play page instead (see
    # pages/play.py's own module docstring). This is purely completed/
    # pending-signoff history now.
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
            _account_subnav("profile"),
            dcc.Location(id="round-invite-refresh", refresh=True),
            dcc.Location(id="club-invite-refresh", refresh=True),
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
                                # Starting a round now lives on the Play
                                # page (see pages/play.py) -- this just
                                # links there rather than duplicating the
                                # Start New Round modal on this page too.
                                action=dcc.Link(
                                    "Start a Round",
                                    href="/play",
                                    className="t3g-panel-action-button",
                                    style={"textDecoration": "none"},
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
        _round_scorecard_card(
            r,
            [
                {
                    "initial": player_info.get("initial", "Y"),
                    "label": player_info.get("label", "You"),
                    "holes": r.get("holes") or [],
                    "handicap": r.get("handicap"),
                }
            ],
        )
        for r in page_rounds
    ]

    label = f"{page + 1} of {total_pages}"
    return cards, page <= 0, page >= total_pages - 1, label


@callback(
    Output("round-invite-refresh", "href"),
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
        # href (not pathname) -- pathname gets percent-encoded when it
        # contains a "?", corrupting the query string; and even without
        # one, dcc.Location only reloads when the value it's given differs
        # from what's already loaded, so a cache-busting suffix matters
        # anywhere the target page could be the one you're already on.
        # Same fix as tournament.py/club.py/my_account.py's redirects.
        # Target /play (not /live-round -- that page was renamed, see
        # pages/play.py) with the round_id explicit so the accepted round's
        # scorecard opens directly instead of landing on the Play hub.
        return f"/play?round_id={triggered_id['round_id']}&_r={time.time()}", ""

    try:
        detail = response.json().get("detail", "Couldn't accept that invite.")
    except ValueError:
        detail = "Couldn't accept that invite."
    return dash.no_update, detail


@callback(
    Output("round-invite-refresh", "href", allow_duplicate=True),
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

    # Cache-busted -- this panel now lives on /my-account/profile (moved
    # off "/" when Home became the activity feed), so the redirect target
    # has to follow it -- a bare "/" would silently bounce the player onto
    # the new Home feed instead of refreshing this page.
    return f"/my-account/profile?_r={time.time()}"

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
    Output("club-invite-refresh", "href"),
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
        # href + cache-bust -- this panel now lives on /my-account/profile
        # (moved off "/" when Home became the activity feed), so the
        # redirect target has to follow it, same fix as
        # decline_round_invite/decline_club_invite below.
        return f"/my-account/profile?_r={time.time()}", ""

    try:
        detail = response.json().get("detail", "Couldn't accept that invite.")
    except ValueError:
        detail = "Couldn't accept that invite."
    return dash.no_update, detail


@callback(
    Output("club-invite-refresh", "href", allow_duplicate=True),
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

    # Same redirect fix as accept_club_invite/decline_round_invite above.
    return f"/my-account/profile?_r={time.time()}"