# target path: frontend/src/pages/profile.py (new file)
"""
The friends-visible player profile page -- reached by clicking a name on
the Friends page (see friends.py's _friend_row). Everything it shows
comes from one aggregate call to GET /players/{player_id}/profile, which
the backend gates server-side (see get_player_profile in backend/
services/players.py): the viewer has to be either the profile's own
player or a confirmed friend of theirs, or the request comes back 403.

The Handicap panel and Game Analysis charts deliberately duplicate a
handful of small pure render functions from home.py and analysis.py
(_round_scorecard_card, the _handicap_* trend/rounds helpers, _build_
figure/_build_analysis_body) rather than importing them cross-page --
same "small per-page copies instead of a shared module" convention this
app already follows elsewhere (see analysis.py's own module docstring),
and it keeps this page's component ids/callbacks fully self-contained
rather than risking a second dash.register_page("/", ...) call if page
auto-discovery and a plain cross-module import ever raced each other.
"""
import dash
import plotly.graph_objects as go
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from components.scorecard import format_handicap, history_score_mark_class
from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path_template="/players/<player_id>", name="Player Profile")

_ROLLING_AVG_COLOR = "#c21861"
_RAW_POINT_COLOR = "#c7cad1"


def _not_found_page():
    return html.Div(
        className="t3g-page",
        children=html.Div(
            className="t3g-panel",
            children=html.Div(
                html.P("Player not found.", className="t3g-empty-state"),
                className="t3g-panel-body",
            ),
        ),
    )


def _locked_page():
    # 403 from the backend -- not a "something broke" error, just "you're
    # not confirmed friends yet". Points back at Friends rather than
    # trying to offer a one-click "send request" here, so this page
    # doesn't need its own friend-request callback wired up just for this
    # one edge case.
    return html.Div(
        className="t3g-page",
        children=html.Div(
            className="t3g-panel",
            children=html.Div(
                className="t3g-panel-body",
                children=[
                    html.P(
                        "This profile is only visible to confirmed friends.",
                        className="t3g-empty-state mb-2",
                    ),
                    dcc.Link("Add them as a friend", href="/friends", className="t3g-panel-action-button"),
                ],
            ),
        ),
    )


def _player_display_name(player):
    full_name = f"{player.get('first_name', '')} {player.get('surname', '')}".strip()
    return player.get("nickname") or full_name or "Unknown Player"


def _profile_header(player):
    full_name = f"{player.get('first_name', '')} {player.get('surname', '')}".strip()
    display_name = _player_display_name(player)
    photo_url = player.get("profile_picture_url")
    initial = (player.get("first_name") or display_name or "?")[0].upper()

    if photo_url:
        avatar = html.Img(src=photo_url, className="t3g-profile-photo t3g-player-profile-avatar")
    else:
        avatar = html.Div(initial, className="t3g-player-profile-avatar-fallback")

    meta_children = []
    # Only shown when the nickname differs from the display name -- if
    # there's no nickname, display_name already *is* the full name, so
    # repeating it underneath would just be noise.
    if player.get("nickname") and full_name:
        meta_children.append(html.Div(full_name, className="t3g-player-profile-fullname"))
    if player.get("home_course"):
        meta_children.append(
            html.Div(f"Home course: {player['home_course']}", className="t3g-player-profile-meta-line")
        )

    return html.Div(
        className="t3g-player-profile-header",
        children=[
            avatar,
            html.Div(
                className="t3g-player-profile-header-info",
                children=[html.H2(display_name, className="t3g-player-profile-name"), *meta_children],
            ),
        ],
    )


# ---------------------------------------------------------------------
# Handicap panel -- mirrors home.py's own Trend / Contributing Rounds
# toggle, duplicated here under a "profile-" id prefix (own dcc.Store
# pair, own toggle buttons, own callback below) so this page's component
# ids never collide with home.py's identically-purposed ones.
# ---------------------------------------------------------------------

def _handicap_trend_figure(history):
    ordered = list(reversed(history))  # API returns most-recent-first; chart wants chronological
    dates = [h["valid_from"] for h in ordered]
    values = [h["handicap"] for h in ordered]

    fig = go.Figure()
    # Same treatment as home.py's identically-purposed chart (and the
    # Game Analysis trend charts below): smoothed spline + gradient fill,
    # white-ringed ("halo") marker, no yaxis_title (the panel's own
    # "Handicap" navbar title already says what this is), padded y-range,
    # and a solid pill "current value" badge floated above the last point
    # rather than off to its side.
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
        fig.add_annotation(
            x=dates[-1],
            y=values[-1],
            text=f"<b>{values[-1]}</b>",
            showarrow=False,
            xanchor="center",
            yshift=20,
            font=dict(color="#ffffff", size=11),
            bgcolor="#c21861",
            bordercolor="#c21861",
            borderpad=4,
        )
    return fig


def _handicap_trend_view(history):
    if len(history) < 2:
        return html.P(
            "Not enough handicap history yet.",
            className="t3g-empty-state",
        )
    return dcc.Graph(
        figure=_handicap_trend_figure(history),
        config=GRAPH_CONFIG,
        style={"width": "100%", "height": "280px"},
    )


def _handicap_round_card(r):
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
            "No rounds counting toward their handicap yet.",
            className="t3g-empty-state",
        )

    return html.Div(
        [
            html.Div([_handicap_round_card(r) for r in rounds], className="t3g-handicap-rounds-grid"),
            html.P(
                "Highlighted cards are currently counting toward their handicap. "
                "Top-right is the adjusted score, bottom-right is the course's slope rating.",
                className="t3g-empty-state mt-2",
            ),
        ]
    )


def _profile_handicap_panel(current_handicap, history, breakdown):
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
                                id="profile-handicap-view-trend",
                                className="t3g-handicap-toggle-button",
                                n_clicks=0,
                            ),
                            html.Button(
                                "Contributing Rounds",
                                id="profile-handicap-view-rounds",
                                className="t3g-handicap-toggle-button t3g-handicap-toggle-button--active",
                                n_clicks=0,
                            ),
                        ],
                    ),
                    dcc.Store(id="profile-handicap-history-store", data=history),
                    dcc.Store(id="profile-handicap-breakdown-store", data=breakdown),
                    html.Div(id="profile-handicap-panel-content", children=_handicap_rounds_view(breakdown)),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------
# Game Analysis -- duplicated from analysis.py's _build_figure/_build_
# analysis_body, unchanged (no ids inside either, so no collision risk
# even if it were imported instead -- duplicated anyway for the same
# self-containment reasoning as the handicap panel above).
# ---------------------------------------------------------------------

def _apply_chart_theme(fig, height=340):
    # Restyled after feedback that the charts read as too heavy/busy --
    # smaller, muted tick text; a dashed (not solid) gridline so the grid
    # recedes into the background instead of competing with the data; and
    # the y-axis moved to the right, which is both a lighter/more modern
    # placement and means the left edge no longer needs any margin at all
    # for tick labels.
    fig.update_layout(
        autosize=True,
        margin=dict(l=8, r=40, t=28, b=28),
        height=height,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#1e2a47"),
        showlegend=False,
        hoverlabel=dict(bgcolor="#1e2a47", bordercolor="#1e2a47", font=dict(color="#ffffff")),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, nticks=4, tickfont=dict(size=10, color="#9aa0b0"))
    fig.update_yaxes(
        side="right",
        showgrid=True,
        gridcolor="#e7e9f0",
        griddash="dash",
        gridwidth=1,
        zeroline=False,
        nticks=4,
        tickfont=dict(size=10, color="#9aa0b0"),
    )


# Every dcc.Graph in this module passes this exact dict -- responsive:
# True is what actually lets Plotly resize the chart to fit whatever
# width its .t3g-analysis-card is given, instead of rendering at a fixed
# ~700px regardless of container size. Without it, two side-by-side
# charts in the 1fr/1fr grid were each forcing their own column (and so
# the whole grid, and the page around it) wider than the viewport, which
# is what was actually causing the "wider than the page" / "doesn't
# wrap" symptom -- the grid's own @media breakpoint below 900px was
# never the problem, the charts refusing to shrink into it was.
GRAPH_CONFIG = {"displayModeBar": False, "responsive": True}


def _padded_range(values, factor=1.5):
    """A y-axis range that's `factor` times as wide as the data's own
    min-to-max span, centered on the data -- rather than the range a
    zero-anchored fill (fill="tozeroy") would otherwise imply. A trend
    line living in the low 30s doesn't need to share its chart with 30
    points of dead space down to zero; letting the axis hug the data
    (with room to breathe on both sides) is what actually shows the
    week-to-week movement instead of flattening it into a thin band at
    the top of the plot."""
    if not values:
        return None
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        # A flat/single-point series has no span to scale from -- pad by
        # a fraction of the value itself (with a floor) so the line
        # doesn't render glued to the very top or bottom edge.
        pad = max(abs(hi) * 0.15, 1)
        return [lo - pad, hi + pad]
    extra = (span * factor - span) / 2
    return [lo - extra, hi + extra]


def _build_figure(points, raw_field, avg_field, hover_suffix=""):
    # Only plot rounds where this particular stat has a rolling average --
    # that's exactly the subset the backend computed the average over, so
    # a round missing putts data (say) doesn't show up as a gap or a zero.
    filtered = [p for p in points if p.get(avg_field) is not None]
    dates = [p["date"] for p in filtered]
    raw_values = [p[raw_field] for p in filtered]
    avg_values = [p[avg_field] for p in filtered]

    fig = go.Figure()
    # Raw per-round points sit behind the trend line, small and faint --
    # still there on hover for the exact figure, but not competing with
    # the rolling average for attention the way two equal-weight traces
    # (plus a legend to tell them apart) used to.
    fig.add_trace(go.Scatter(
        x=dates,
        y=raw_values,
        mode="markers",
        name="Per round",
        marker=dict(color=_RAW_POINT_COLOR, size=5, opacity=0.5, line=dict(width=0)),
        hovertemplate=f"%{{x}}<br>%{{y}}{hover_suffix}<extra></extra>",
    ))
    # The rolling average is the one line doing the actual storytelling --
    # a thin smoothed spline with a small white-ringed ("halo") marker.
    # No area fill underneath it any more -- a lighter, line-only look
    # reads as less "busy" than a gradient fill competing with the grid
    # and the raw points for attention.
    fig.add_trace(go.Scatter(
        x=dates,
        y=avg_values,
        mode="lines+markers",
        name="5-round rolling avg",
        line=dict(color=_ROLLING_AVG_COLOR, width=2, shape="spline", smoothing=0.6),
        marker=dict(color=_ROLLING_AVG_COLOR, size=6, line=dict(color="#ffffff", width=1.5)),
        hovertemplate=f"%{{x}}<br>%{{y}}{hover_suffix} avg<extra></extra>",
    ))
    _apply_chart_theme(fig)
    fig.update_xaxes(tickformat="%b %d")
    fig.update_yaxes(range=_padded_range(raw_values + avg_values))
    if avg_values:
        # Always-visible "where do I stand right now" tag at the end of
        # the trend line, styled as a solid pill (not just floating
        # text) -- the same "last value" callout a stock/fitness-tracker
        # chart uses. Hovering still gives every other point's exact
        # figure via the tooltip.
        fig.add_annotation(
            x=dates[-1],
            y=avg_values[-1],
            text=f"<b>{avg_values[-1]}{hover_suffix}</b>",
            showarrow=False,
            xanchor="center",
            yshift=20,  # floats the badge above the point instead of off to its side
            font=dict(color="#ffffff", size=11),
            bgcolor=_ROLLING_AVG_COLOR,
            bordercolor=_ROLLING_AVG_COLOR,
            borderpad=4,
        )
    return fig


# Golf-scoring convention: E for level par rather than "0", which reads
# as "no score" rather than "exactly on target". Matches how a real
# scorecard or broadcast graphic marks an even score.
def _format_score_to_par(value):
    if value == 0:
        return "E"
    return f"+{value}" if value > 0 else f"{value}"


def _add_bar_badges(fig, labels, values, colors, text_fn):
    """Per-bar value labels as solid colored pills sitting just past the
    end of each bar, instead of go.Bar's own plain-text `text` -- reads
    as a designed dashboard rather than default chart-library labels.
    Placed above the bar for a positive value, below for a negative one
    (only the Score to Par chart ever has negative bars), so the badge
    never overlaps the bar it's labelling."""
    for label, value, color in zip(labels, values, colors):
        fig.add_annotation(
            x=label,
            y=value,
            yshift=16 if value >= 0 else -16,
            text=f"<b>{text_fn(value)}</b>",
            showarrow=False,
            font=dict(color="#ffffff", size=12),
            bgcolor=color,
            bordercolor=color,
            borderpad=5,
        )


# Same diverging colors the leaderboard's own total pill already uses for
# under/level/over par (see .t3g-leaderboard-total-pill--under/--even/
# --over in club.css) -- carried over here so "better than par" reads as
# the same color everywhere in the app, not a different one per chart.
_PAR_TYPE_COLORS = {"under": "#dc2626", "even": "#4b5468", "over": "#1e2a47"}


def _par_type_figure(par_type_breakdown):
    played = [b for b in par_type_breakdown if b.get("holes_played")]
    if not played:
        return None

    labels = [f"Par {b['par']}" for b in played]
    values = [b["avg_score_to_par"] for b in played]
    colors = [
        _PAR_TYPE_COLORS["under"] if v < 0 else _PAR_TYPE_COLORS["over"] if v > 0 else _PAR_TYPE_COLORS["even"]
        for v in values
    ]

    fig = go.Figure(go.Bar(
        x=labels,
        y=values,
        marker=dict(color=colors, cornerradius=8),
        text=[_format_score_to_par(v) for v in values],
        textposition="none",  # kept on the trace for hovertemplate only -- see _add_bar_badges for the visible label
        hovertemplate="%{x}<br>%{text} to par<extra></extra>",
        width=0.55,
    ))
    _apply_chart_theme(fig, height=300)
    # A real reference line at "level par" (not just wherever y=0 happens
    # to land from the axis's own auto-range) -- makes it immediately
    # obvious which bars are actually costing strokes versus gaining them
    # back, at a glance rather than reading each label.
    fig.update_yaxes(zeroline=True, zerolinecolor="#dbe1ea", zerolinewidth=2)
    _add_bar_badges(fig, labels, values, colors, _format_score_to_par)
    return fig


# Same green/navy/pink language _round_scorecard_card's birdie/bogey
# marks already use (see history_score_mark_class / .t3g-history-score-*
# in club.css/home.css) -- birdie green, bogey/double-bogey pink, with
# level par landing on the brand navy in between rather than a washed-out
# grey that would read as "no data".
_SCORING_BUCKET_LABELS = {
    "birdie_or_better": "Birdie+",
    "par": "Par",
    "bogey": "Bogey",
    "double_bogey_plus": "Dbl Bogey+",
}
_SCORING_BUCKET_COLORS = {
    "birdie_or_better": "#2a9d3f",
    "par": "#1e2a47",
    "bogey": "#e8a0c2",
    "double_bogey_plus": "#c21861",
}
_SCORING_BUCKET_ORDER = ["birdie_or_better", "par", "bogey", "double_bogey_plus"]


def _scoring_breakdown_figure(scoring_breakdown):
    by_category = {b["category"]: b for b in scoring_breakdown}
    ordered = [by_category[c] for c in _SCORING_BUCKET_ORDER if c in by_category]
    if not ordered or all(b.get("avg_per_round") is None for b in ordered):
        return None

    labels = [_SCORING_BUCKET_LABELS[b["category"]] for b in ordered]
    values = [b["avg_per_round"] or 0 for b in ordered]
    colors = [_SCORING_BUCKET_COLORS[b["category"]] for b in ordered]

    fig = go.Figure(go.Bar(
        x=labels,
        y=values,
        marker=dict(color=colors, cornerradius=8),
        text=[f"{v:g}" for v in values],
        textposition="none",  # kept on the trace for hovertemplate only -- see _add_bar_badges for the visible label
        hovertemplate="%{x}: %{y} per round<extra></extra>",
        width=0.55,
    ))
    _apply_chart_theme(fig, height=300)
    _add_bar_badges(fig, labels, values, colors, lambda v: f"{v:g}")
    return fig


# Order matches backend/services/rounds.py's _DISTANCE_BINS -- short holes
# to long, left to right, same as par_type_figure orders Par 3/4/5 short
# to long rather than by whatever order the backend dict happens to
# iterate in.
_DISTANCE_BIN_ORDER = ["< 150y", "150-249y", "250-349y", "350-449y", "450y+"]


def _distance_profile_figure(distance_breakdown):
    by_bin = {b["bin"]: b for b in distance_breakdown}
    played = [by_bin[b] for b in _DISTANCE_BIN_ORDER if b in by_bin and by_bin[b].get("holes_played")]
    if not played:
        return None

    labels = [b["bin"] for b in played]
    values = [b["avg_strokes"] for b in played]

    fig = go.Figure(go.Bar(
        x=labels,
        y=values,
        marker=dict(color=_ROLLING_AVG_COLOR, cornerradius=8),
        text=[f"{v:g}" for v in values],
        textposition="none",  # kept on the trace for hovertemplate only -- see _add_bar_badges for the visible label
        hovertemplate="%{x}<br>%{text} shots avg<extra></extra>",
        width=0.55,
    ))
    _apply_chart_theme(fig, height=300)
    _add_bar_badges(fig, labels, values, [_ROLLING_AVG_COLOR] * len(labels), lambda v: f"{v:g}")
    return fig


# Three ways to slice the same underlying round list (see backend's
# get_player_scoring_history) rather than three separate API calls --
# "validated" (status == completed, i.e. counts toward Handicap Index)
# is the default since that's the number that actually matters for
# handicap purposes; "tournament" and "all" are one tap away via the
# same toggle row the Handicap panel's Trend/Contributing Rounds switch
# already uses (.t3g-handicap-toggle).
_SCORING_HISTORY_VIEWS = ("validated", "tournament", "all")


def _filter_scoring_history(points, view):
    if view == "validated":
        return [p for p in points if p.get("validated")]
    if view == "tournament":
        return [p for p in points if p.get("is_tournament")]
    return list(points)


def _scoring_history_figure(points, view="validated", window=10):
    """One bar per round, in play order -- round number on the x-axis
    rather than the actual date, so a gap of a few weeks between rounds
    doesn't stretch the bars apart or bunch a run of rounds played on
    consecutive days together. The real date is still there on hover
    (via customdata), just not driving the axis. A trailing `window`-
    round rolling average line sits on top of the bars -- computed over
    whichever view is currently filtered to (Validated/Tournament/All),
    since a 10-round average of validated rounds and a 10-round average
    of tournament rounds are two different numbers even for the same
    player."""
    filtered = _filter_scoring_history(points, view)
    if not filtered:
        return None

    dates = [p["date"] for p in filtered]
    values = [p["total_strokes"] for p in filtered]
    round_numbers = list(range(1, len(filtered) + 1))

    rolling_avg = []
    for i in range(len(values)):
        window_vals = values[max(0, i - window + 1):i + 1]
        rolling_avg.append(round(sum(window_vals) / len(window_vals), 1))

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=round_numbers,
        y=values,
        customdata=dates,
        marker=dict(color="#1e2a47", cornerradius=6),
        hovertemplate="%{customdata}<br>%{y} shots<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=round_numbers,
        y=rolling_avg,
        customdata=dates,
        mode="lines+markers",
        line=dict(color=_ROLLING_AVG_COLOR, width=2, shape="spline", smoothing=0.6),
        marker=dict(color=_ROLLING_AVG_COLOR, size=6, line=dict(color="#ffffff", width=1.5)),
        hovertemplate=f"%{{customdata}}<br>%{{y}} avg (last {window})<extra></extra>",
    ))
    _apply_chart_theme(fig, height=300)
    fig.update_yaxes(range=_padded_range(values + rolling_avg))
    if rolling_avg:
        fig.add_annotation(
            x=round_numbers[-1],
            y=rolling_avg[-1],
            text=f"<b>{rolling_avg[-1]}</b>",
            showarrow=False,
            xanchor="center",
            yshift=20,
            font=dict(color="#ffffff", size=11),
            bgcolor=_ROLLING_AVG_COLOR,
            bordercolor=_ROLLING_AVG_COLOR,
            borderpad=4,
        )
    return fig


def _build_analysis_body(points, scoring_profile=None):
    scoring_profile = scoring_profile or {}
    has_putts = any(p.get("putts_rolling_avg") is not None for p in points)
    has_fairway = any(p.get("fairway_rolling_avg") is not None for p in points)
    par_type_fig = _par_type_figure(scoring_profile.get("par_type_breakdown") or [])
    scoring_fig = _scoring_breakdown_figure(scoring_profile.get("scoring_breakdown") or [])

    if not has_putts and not has_fairway and not par_type_fig and not scoring_fig:
        return html.P(
            "No completed rounds with scoring data yet.",
            className="t3g-empty-state",
        )

    # Fixed pixel height on every Graph's style (matching its figure's own
    # layout height) -- config.responsive=True re-measures the container on
    # every resize and redraws at that size, height included. Without a
    # fixed CSS height the container's height comes from the chart's own
    # rendered content, which is circular: draw -> box grows to fit ->
    # resize observer fires -> draw taller -> box grows again, with no
    # ceiling. See analysis.py's _build_analysis_body for the full note.
    cards = []
    if has_putts:
        cards.append(
            html.Div(
                className="t3g-analysis-card",
                children=[
                    html.H4("Putts per Round", className="t3g-analysis-card-title"),
                    dcc.Graph(
                        figure=_build_figure(points, "putts_total", "putts_rolling_avg"),
                        config=GRAPH_CONFIG,
                        style={"width": "100%", "height": "340px"},
                    ),
                ],
            )
        )
    if has_fairway:
        cards.append(
            html.Div(
                className="t3g-analysis-card",
                children=[
                    html.H4("Fairways Hit (%)", className="t3g-analysis-card-title"),
                    dcc.Graph(
                        figure=_build_figure(
                            points, "fairway_pct", "fairway_rolling_avg", hover_suffix="%"
                        ),
                        config=GRAPH_CONFIG,
                        style={"width": "100%", "height": "340px"},
                    ),
                ],
            )
        )
    if par_type_fig is not None:
        cards.append(
            html.Div(
                className="t3g-analysis-card",
                children=[
                    html.H4("Avg Score to Par by Hole Type", className="t3g-analysis-card-title"),
                    dcc.Graph(
                        figure=par_type_fig,
                        config=GRAPH_CONFIG,
                        style={"width": "100%", "height": "300px"},
                    ),
                ],
            )
        )
    if scoring_fig is not None:
        cards.append(
            html.Div(
                className="t3g-analysis-card",
                children=[
                    html.H4("Scoring Breakdown (avg per round)", className="t3g-analysis-card-title"),
                    dcc.Graph(
                        figure=scoring_fig,
                        config=GRAPH_CONFIG,
                        style={"width": "100%", "height": "300px"},
                    ),
                ],
            )
        )
    return html.Div(className="t3g-analysis-grid", children=cards)


# Paginated variant of the above, used only on this page -- a friend
# looking at someone else's profile is dropping in for a quick look, not
# settling in the way the profile's own owner might on their own full
# Analysis tab (which keeps the plain grid, unchanged). One chart at a
# time, flipped through with Prev/Next, keeps the panel compact instead
# of stacking all four charts down the page.
def _build_analysis_body_paginated(points, scoring_profile=None, distance_profile=None, scoring_history=None):
    scoring_profile = scoring_profile or {}
    distance_profile = distance_profile or {}
    scoring_history = scoring_history or []
    has_putts = any(p.get("putts_rolling_avg") is not None for p in points)
    has_fairway = any(p.get("fairway_rolling_avg") is not None for p in points)
    par_type_fig = _par_type_figure(scoring_profile.get("par_type_breakdown") or [])
    scoring_fig = _scoring_breakdown_figure(scoring_profile.get("scoring_breakdown") or [])
    distance_fig = _distance_profile_figure(distance_profile.get("distance_breakdown") or [])
    default_scoring_history_view = next(
        (v for v in _SCORING_HISTORY_VIEWS if _filter_scoring_history(scoring_history, v)),
        "validated",
    )
    scoring_history_fig = _scoring_history_figure(scoring_history, default_scoring_history_view)

    # Each page is (title, [children]) rather than (title, figure, height)
    # -- every other page is just a single Graph, but Scoring History
    # also needs its own Store + Validated/Tournament/All toggle row
    # ahead of the Graph, so the card's children need to be an arbitrary
    # list, not always exactly one figure.
    pages = []
    if has_putts:
        pages.append((
            "Putts per Round",
            [dcc.Graph(
                figure=_build_figure(points, "putts_total", "putts_rolling_avg"),
                config=GRAPH_CONFIG,
                style={"width": "100%", "height": "340px"},
            )],
        ))
    if has_fairway:
        pages.append((
            "Fairways Hit (%)",
            [dcc.Graph(
                figure=_build_figure(points, "fairway_pct", "fairway_rolling_avg", hover_suffix="%"),
                config=GRAPH_CONFIG,
                style={"width": "100%", "height": "340px"},
            )],
        ))
    if par_type_fig is not None:
        pages.append((
            "Avg Score to Par by Hole Type",
            [dcc.Graph(figure=par_type_fig, config=GRAPH_CONFIG, style={"width": "100%", "height": "300px"})],
        ))
    if scoring_fig is not None:
        pages.append((
            "Scoring Breakdown (avg per round)",
            [dcc.Graph(figure=scoring_fig, config=GRAPH_CONFIG, style={"width": "100%", "height": "300px"})],
        ))
    if distance_fig is not None:
        pages.append((
            "Avg Shots by Hole Distance",
            [dcc.Graph(figure=distance_fig, config=GRAPH_CONFIG, style={"width": "100%", "height": "300px"})],
        ))
    if scoring_history_fig is not None:
        def _toggle_button(label, view_key):
            active = view_key == default_scoring_history_view
            return html.Button(
                label,
                id=f"profile-scoring-history-view-{view_key}",
                className=(
                    "t3g-handicap-toggle-button t3g-handicap-toggle-button--active"
                    if active
                    else "t3g-handicap-toggle-button"
                ),
                n_clicks=0,
            )

        pages.append((
            "Scoring History",
            [
                dcc.Store(id="profile-scoring-history-store", data=scoring_history),
                html.Div(
                    className="t3g-handicap-toggle",
                    children=[
                        _toggle_button("Validated", "validated"),
                        _toggle_button("Tournament", "tournament"),
                        _toggle_button("All", "all"),
                    ],
                ),
                dcc.Graph(
                    id="profile-scoring-history-graph",
                    figure=scoring_history_fig,
                    config=GRAPH_CONFIG,
                    style={"width": "100%", "height": "300px"},
                ),
            ],
        ))

    if not pages:
        return html.P(
            "No completed rounds with scoring data yet.",
            className="t3g-empty-state",
        )

    # Every card is built and mounted up front -- only one is ever shown
    # at a time (display:none on the rest), toggled by the pagination
    # callback below. Same show/hide-via-style pattern this page's own
    # Trend/Contributing Rounds toggle and analysis.py's Rounds/Analysis
    # subnav both already use, rather than re-rendering the Graph on
    # every click.
    cards = [
        html.Div(
            id={"type": "profile-analysis-page", "index": i},
            style={} if i == 0 else {"display": "none"},
            className="t3g-analysis-card",
            children=[html.H4(title, className="t3g-analysis-card-title"), *page_children],
        )
        for i, (title, page_children) in enumerate(pages)
    ]

    children = [dcc.Store(id="profile-analysis-page-index", data=0), *cards]

    if len(pages) > 1:
        children.append(
            html.Div(
                className="t3g-analysis-pagination",
                children=[
                    html.Button(
                        "\u2039",
                        id="profile-analysis-prev-button",
                        className="t3g-analysis-pagination-button",
                        n_clicks=0,
                        **{"aria-label": "Previous chart"},
                    ),
                    html.Span(f"1 of {len(pages)}", id="profile-analysis-page-label",
                              className="t3g-analysis-pagination-label"),
                    html.Button(
                        "\u203a",
                        id="profile-analysis-next-button",
                        className="t3g-analysis-pagination-button",
                        n_clicks=0,
                        **{"aria-label": "Next chart"},
                    ),
                ],
            )
        )

    return html.Div(className="t3g-analysis-paginated", children=children)


# ---------------------------------------------------------------------
# Recent Rounds -- duplicated from home.py's _round_scorecard_card,
# adapted to a simple title/subtitle header (club/course/tees/date)
# instead of round_header_label's live/tournament-badge handling --
# recent rounds here are always completed rounds, so none of that
# applies. Always rendered with a single player_rows entry (the
# profile's own player, not the viewer).
# ---------------------------------------------------------------------

def _round_scorecard_card(round_data, player_rows):
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

    club_name = round_data.get("club_name") or "Round"
    course_bits = [b for b in [round_data.get("course_name"), round_data.get("tee_name")] if b]
    date_bit = round_data.get("completed_at")
    subtitle_bits = course_bits + ([date_bit[:10]] if date_bit else [])
    subtitle = " — ".join(subtitle_bits)

    header_children = [html.Span(club_name, className="t3g-round-card-title")]
    if subtitle:
        header_children.append(html.Span(subtitle, className="t3g-round-card-subtitle"))

    return html.Div(
        className="t3g-round-card",
        children=[
            html.Div(header_children, className="t3g-round-card-header"),
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


def _recent_rounds_panel(recent_rounds, player):
    label = _player_display_name(player)
    initial = (player.get("first_name") or label or "?")[0].upper()

    completed = [r for r in recent_rounds if r.get("status") == "completed"][:5]

    if not completed:
        body = html.P("No completed rounds yet.", className="t3g-empty-state")
    else:
        body = html.Div(
            [
                _round_scorecard_card(
                    r,
                    [{"initial": initial, "label": label, "holes": r.get("holes") or [], "handicap": r.get("handicap")}],
                )
                for r in completed
            ],
            # Same .t3g-rounds-list gap treatment home.py's own Rounds
            # History panel uses between cards -- without it every card
            # here butts straight up against the next with no visible
            # separation at all.
            className="t3g-rounds-list",
        )

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Recent Rounds"),
            html.Div(body, className="t3g-panel-body"),
        ],
    )


def layout(player_id=None, **kwargs):
    viewer_id = session.get("player_id")

    if not session.get("logged_in") or not viewer_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="profile-redirect-signin", refresh=True)

    if not player_id:
        return _not_found_page()

    profile_resp = requests.get(
        f"{API_BASE_URL}/players/{player_id}/profile", params={"viewer_player_id": viewer_id}
    )

    if profile_resp.status_code == 403:
        return _locked_page()
    if profile_resp.status_code != 200:
        return _not_found_page()

    profile = profile_resp.json()
    player = profile.get("player") or {}
    current_handicap = profile.get("current_handicap")
    handicap_history = profile.get("handicap_history") or []
    handicap_breakdown = profile.get("handicap_breakdown") or {}
    recent_rounds = profile.get("recent_rounds") or []
    analysis_points = profile.get("analysis_points") or []
    scoring_profile = profile.get("scoring_profile") or {}
    distance_profile = profile.get("distance_profile") or {}
    scoring_history = profile.get("scoring_history") or []

    return html.Div(
        className="t3g-page",
        children=[
            _profile_header(player),
            html.Div(
                className="t3g-panel-grid t3g-player-profile-grid",
                children=[
                    _profile_handicap_panel(current_handicap, handicap_history, handicap_breakdown),
                    html.Div(
                        className="t3g-panel",
                        children=[
                            build_panel_navbar("Game Analysis"),
                            html.Div(
                                _build_analysis_body_paginated(
                                    analysis_points, scoring_profile, distance_profile, scoring_history
                                ),
                                className="t3g-panel-body",
                            ),
                        ],
                    ),
                ],
            ),
            _recent_rounds_panel(recent_rounds, player),
        ],
    )


@callback(
    Output("profile-handicap-panel-content", "children"),
    Output("profile-handicap-view-trend", "className"),
    Output("profile-handicap-view-rounds", "className"),
    Input("profile-handicap-view-trend", "n_clicks"),
    Input("profile-handicap-view-rounds", "n_clicks"),
    State("profile-handicap-history-store", "data"),
    State("profile-handicap-breakdown-store", "data"),
    prevent_initial_call=True,
)
def render_profile_handicap_view(trend_clicks, rounds_clicks, history, breakdown):
    triggered_id = dash.ctx.triggered_id
    base_class = "t3g-handicap-toggle-button"
    active_class = f"{base_class} t3g-handicap-toggle-button--active"

    if triggered_id == "profile-handicap-view-rounds":
        return _handicap_rounds_view(breakdown or {}), base_class, active_class

    return _handicap_trend_view(history or []), active_class, base_class


@callback(
    Output("profile-analysis-page-index", "data"),
    Output({"type": "profile-analysis-page", "index": ALL}, "style"),
    Output("profile-analysis-page-label", "children"),
    Input("profile-analysis-prev-button", "n_clicks"),
    Input("profile-analysis-next-button", "n_clicks"),
    State("profile-analysis-page-index", "data"),
    State({"type": "profile-analysis-page", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def paginate_profile_analysis(prev_clicks, next_clicks, current_index, page_ids):
    # Wraps in both directions (Next past the last chart lands back on
    # the first, Prev before the first lands on the last) rather than
    # disabling the buttons at the ends -- one less state to track, and
    # it's a small enough set of charts that looping around is more
    # convenient than a hard stop.
    count = len(page_ids)
    if count == 0:
        raise PreventUpdate

    if dash.ctx.triggered_id == "profile-analysis-prev-button":
        new_index = (current_index - 1) % count
    elif dash.ctx.triggered_id == "profile-analysis-next-button":
        new_index = (current_index + 1) % count
    else:
        new_index = current_index

    styles = [{} if i == new_index else {"display": "none"} for i in range(count)]
    return new_index, styles, f"{new_index + 1} of {count}"


@callback(
    Output("profile-scoring-history-graph", "figure"),
    Output("profile-scoring-history-view-validated", "className"),
    Output("profile-scoring-history-view-tournament", "className"),
    Output("profile-scoring-history-view-all", "className"),
    Input("profile-scoring-history-view-validated", "n_clicks"),
    Input("profile-scoring-history-view-tournament", "n_clicks"),
    Input("profile-scoring-history-view-all", "n_clicks"),
    State("profile-scoring-history-store", "data"),
    prevent_initial_call=True,
)
def switch_profile_scoring_history_view(validated_clicks, tournament_clicks, all_clicks, points):
    base_class = "t3g-handicap-toggle-button"
    active_class = f"{base_class} t3g-handicap-toggle-button--active"

    triggered_id = dash.ctx.triggered_id
    view = {
        "profile-scoring-history-view-validated": "validated",
        "profile-scoring-history-view-tournament": "tournament",
        "profile-scoring-history-view-all": "all",
    }.get(triggered_id, "validated")

    fig = _scoring_history_figure(points or [], view)
    if fig is None:
        # No rounds in this particular view -- leave the previous figure
        # up rather than swapping in nothing (same call as analysis.py's
        # switch_scoring_history_view).
        raise PreventUpdate

    return (
        fig,
        active_class if view == "validated" else base_class,
        active_class if view == "tournament" else base_class,
        active_class if view == "all" else base_class,
    )