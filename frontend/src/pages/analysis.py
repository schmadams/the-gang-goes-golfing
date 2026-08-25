# target path: frontend/src/pages/analysis.py (full replacement)
"""
Analysis is the canonical merged page (was two pages: Scoring History and
Analysis) -- a two-tab subnav lets a player switch between their
historical rounds and their stat trends, reusing tournament.py's exact
tab-bar visual language (.t3g-tournament-subnav/-tabs/-tab(--active)) per
an explicit ask for "a club tournament style subnav" here too.

pages/scoring_history.py still exists, registered at its old /scoring-
history path, but only as a redirect to /analysis?tab=rounds now -- so an
old bookmark or link still lands somewhere useful instead of 404ing.
"""
import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from components.scorecard import (
    format_handicap,
    history_score_mark_class,
    live_badge,
    pending_signoff_badge,
    round_header_label,
)
from config import API_BASE_URL

dash.register_page(__name__, path="/analysis", name="Analysis")

_TAB_BUTTON_BASE = "t3g-tournament-tab"
_TAB_BUTTON_ACTIVE = "t3g-tournament-tab t3g-tournament-tab--active"
_SCORING_TAB_KEYS = ("rounds", "analysis")

_ROLLING_AVG_COLOR = "#c21861"
_RAW_POINT_COLOR = "#c7cad1"


def _tab_visibility(active_tab):
    """(styles, classes) for both tab panels/buttons at page-load time,
    picked from a plain ?tab= query value -- same idea as tournament.py's
    own _tab_visibility, just two tabs instead of four. Anything
    unrecognized (including no ?tab= at all) falls back to "rounds"."""
    hidden = {"display": "none"}
    shown = {}
    key = active_tab if active_tab in _SCORING_TAB_KEYS else "rounds"
    index = _SCORING_TAB_KEYS.index(key)

    styles = tuple(shown if i == index else hidden for i in range(2))
    classes = tuple(_TAB_BUTTON_ACTIVE if i == index else _TAB_BUTTON_BASE for i in range(2))
    return styles, classes


def _scoring_subnav(tab_classes):
    rounds_class, analysis_class = tab_classes
    return html.Div(
        className="t3g-tournament-subnav",
        children=html.Div(
            className="t3g-tournament-subnav-inner",
            children=html.Div(
                className="t3g-tournament-tabs",
                children=[
                    html.Button(
                        "Rounds", id="scoring-tab-rounds-button", className=rounds_class, n_clicks=0
                    ),
                    html.Button(
                        "Analysis", id="scoring-tab-analysis-button", className=analysis_class, n_clicks=0
                    ),
                ],
            ),
        ),
    )


def _fairway_cell(hole):
    # Not a meaningful stat on a par 3 (see live_round.py) -- shown as a
    # dash rather than a false "No".
    if hole.get("par") == 3:
        return html.Td(html.Span("—", className="t3g-history-fairway-na"))
    hit = hole.get("fairway_hit")
    if hit is None:
        return html.Td(html.Span("—", className="t3g-history-fairway-na"))
    if hit:
        return html.Td(html.Span("Y", className="t3g-history-fairway-yes"))
    return html.Td(html.Span("N", className="t3g-history-fairway-no"))


def _fairway_summary(hole_subset):
    # Only par 4s/5s count -- a fraction ("3/5") reads better here than a
    # sum, since the denominator (how many fairways were even in play)
    # varies round to round.
    eligible = [h for h in hole_subset if h.get("par") != 3 and h.get("fairway_hit") is not None]
    if not eligible:
        return "—"
    hit = sum(1 for h in eligible if h.get("fairway_hit"))
    return f"{hit}/{len(eligible)}"


def _round_scorecard_card(round_data, player_initial, player_label, player_id):
    """Full detail version of the Rounds History panel's mini scorecard --
    same Hole/Par/Score rows and OUT/IN/TOT/HCP/NET columns, plus Putts,
    Fairway, Net, and Stableford rows underneath, and a Delete/Scrap/Leave
    button in the header.

    A round shows up here for anyone who belongs to it (list_player_rounds
    covers owner and accepted participant alike), not just its creator --
    so the header action has to be figured out the same way live_round.py's
    own header_actions is: a still-live casual round can only be Scrapped
    by its actual creator (round_data["player_id"], the real rounds.
    player_id column, present here since _build_round_summary spreads
    **round_row); anyone else who's part of it gets Leave instead, which
    only removes their own participation. A live tournament round keeps
    Scrap open to anyone in the grouping, same as it's always been --
    there's no single creator concept there. Anything not live (pending_
    signoff or completed) keeps the plain, unrestricted Delete it's always
    had -- that's "clean up my own history", a different action from
    Scrap/Leave."""
    round_id = round_data["id"]
    is_live = round_data.get("status") == "in_progress"
    is_pending_signoff = round_data.get("status") == "pending_signoff"
    is_tournament_round = bool(round_data.get("tournament_round_id"))
    is_creator = round_data.get("player_id") == player_id

    holes_by_number = {h["hole_number"]: h for h in (round_data.get("holes") or [])}
    front9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(1, 10)]
    back9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(10, 19)]

    def _sum_field(hole_subset, field):
        values = [h.get(field) for h in hole_subset if h.get(field) is not None]
        return sum(values) if values else None

    out_par, in_par = _sum_field(front9, "par"), _sum_field(back9, "par")
    tot_par = out_par + in_par if out_par is not None and in_par is not None else None
    out_strokes, in_strokes = _sum_field(front9, "strokes"), _sum_field(back9, "strokes")
    total_strokes = round_data.get("total_strokes")
    out_putts, in_putts = _sum_field(front9, "putts"), _sum_field(back9, "putts")
    tot_putts = out_putts + in_putts if out_putts is not None and in_putts is not None else None
    out_net, in_net = _sum_field(front9, "net_strokes"), _sum_field(back9, "net_strokes")
    tot_net = out_net + in_net if out_net is not None and in_net is not None else None
    out_pts, in_pts = _sum_field(front9, "stableford_points"), _sum_field(back9, "stableford_points")
    total_stableford = round_data.get("total_stableford")

    handicap = round_data.get("handicap")
    hcp_display = format_handicap(handicap)
    net_display = round(total_strokes - handicap) if (handicap is not None and total_strokes is not None) else "—"

    def _hole_number_cells(hole_subset):
        return [html.Th(str(h["hole_number"])) for h in hole_subset]

    def _plain_cells(hole_subset, field):
        return [html.Td(h.get(field) if h.get(field) is not None else "—") for h in hole_subset]

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

    def _fairway_cells(hole_subset):
        return [_fairway_cell(h) for h in hole_subset]

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
            + _plain_cells(front9, "par")
            + [html.Td(out_par if out_par is not None else "—", className="t3g-history-summary-cell")]
            + _plain_cells(back9, "par")
            + [
                html.Td(in_par if in_par is not None else "—", className="t3g-history-summary-cell"),
                html.Td(tot_par if tot_par is not None else "—", className="t3g-history-summary-cell"),
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    score_row = html.Tr(
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

    putts_row = html.Tr(
        className="t3g-history-detail-row",
        children=(
            [html.Td("Putts", className="t3g-history-row-label")]
            + _plain_cells(front9, "putts")
            + [html.Td(out_putts if out_putts is not None else "—", className="t3g-history-summary-cell")]
            + _plain_cells(back9, "putts")
            + [
                html.Td(in_putts if in_putts is not None else "—", className="t3g-history-summary-cell"),
                html.Td(tot_putts if tot_putts is not None else "—", className="t3g-history-summary-cell"),
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    fairway_row = html.Tr(
        className="t3g-history-detail-row",
        children=(
            [html.Td("Fairway", className="t3g-history-row-label")]
            + _fairway_cells(front9)
            + [html.Td(_fairway_summary(front9), className="t3g-history-summary-cell")]
            + _fairway_cells(back9)
            + [
                html.Td(_fairway_summary(back9), className="t3g-history-summary-cell"),
                html.Td(_fairway_summary(front9 + back9), className="t3g-history-summary-cell"),
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    net_row = html.Tr(
        className="t3g-history-detail-row",
        children=(
            [html.Td("Net", className="t3g-history-row-label")]
            + _plain_cells(front9, "net_strokes")
            + [html.Td(out_net if out_net is not None else "—", className="t3g-history-summary-cell")]
            + _plain_cells(back9, "net_strokes")
            + [
                html.Td(in_net if in_net is not None else "—", className="t3g-history-summary-cell"),
                html.Td(tot_net if tot_net is not None else "—", className="t3g-history-summary-cell"),
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    stableford_row = html.Tr(
        className="t3g-history-detail-row",
        children=(
            [html.Td("Stableford", className="t3g-history-row-label")]
            + _plain_cells(front9, "stableford_points")
            + [html.Td(out_pts if out_pts is not None else "—", className="t3g-history-summary-cell")]
            + _plain_cells(back9, "stableford_points")
            + [
                html.Td(in_pts if in_pts is not None else "—", className="t3g-history-summary-cell"),
                html.Td(total_stableford if total_stableford is not None else "—", className="t3g-history-summary-cell"),
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    if is_live and not is_tournament_round and not is_creator:
        action, action_label = "leave", "Leave"
    elif is_live:
        action, action_label = "scrap", "Scrap"
    else:
        action, action_label = "delete", "Delete"

    header_actions = []
    if is_live:
        header_actions.append(live_badge())
    elif is_pending_signoff:
        # A round can carry this status here even though the player
        # themself has already signed off on it -- it just means someone
        # else in the round hasn't yet. See pages/round_signoff.py for
        # actually approving it; this is read-only context here.
        header_actions.append(pending_signoff_badge())
    header_actions.append(
        html.Button(
            action_label,
            id={"type": "history-round-action", "round_id": round_id, "action": action},
            className="t3g-history-delete-button",
            n_clicks=0,
        )
    )
    header_children = [
        html.Span(round_header_label(round_data), className="t3g-round-card-title"),
        html.Div(header_actions, className="t3g-round-card-header-actions"),
    ]

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
                        html.Tbody([score_row, putts_row, fairway_row, net_row, stableford_row]),
                    ],
                ),
            ),
        ],
    )


# Shared "look" every Player Analysis chart uses -- transparent card
# background (blends straight into the white .t3g-analysis-card instead
# of drawing its own white rectangle), no legend (traces are already
# self-explanatory via hover + the always-on last-value label), and a
# navy-on-white hover box instead of Plotly's plain default one. Applied
# to every figure this page builds (trend lines and the two new bar
# charts alike) so they all read as one consistent chart language rather
# than each looking like a different tool made them.
def _apply_chart_theme(fig, y_title, height=340):
    fig.update_layout(
        autosize=True,
        margin=dict(l=45, r=20, t=16, b=40),
        height=height,
        yaxis_title=y_title,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#1e2a47"),
        showlegend=False,
        hoverlabel=dict(bgcolor="#1e2a47", bordercolor="#1e2a47", font=dict(color="#ffffff")),
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0f1f5", zeroline=False)


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


def _build_figure(points, raw_field, avg_field, y_title, hover_suffix=""):
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
        marker=dict(color=_RAW_POINT_COLOR, size=6, opacity=0.6, line=dict(width=0)),
        hovertemplate=f"%{{x}}<br>%{{y}}{hover_suffix}<extra></extra>",
    ))
    # The rolling average is the one line doing the actual storytelling --
    # a smoothed spline with a soft gradient fill underneath it, and a
    # white-ringed marker (rather than a flat dot sitting directly in the
    # fill) so each point still reads clearly against its own gradient.
    fig.add_trace(go.Scatter(
        x=dates,
        y=avg_values,
        mode="lines+markers",
        name="5-round rolling avg",
        line=dict(color=_ROLLING_AVG_COLOR, width=3, shape="spline", smoothing=0.6),
        marker=dict(color=_ROLLING_AVG_COLOR, size=7, line=dict(color="#ffffff", width=2)),
        fill="tozeroy",
        fillcolor="rgba(194, 24, 97, 0.08)",
        hovertemplate=f"%{{x}}<br>%{{y}}{hover_suffix} avg<extra></extra>",
    ))
    _apply_chart_theme(fig, y_title)
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
            xanchor="left",
            xshift=16,
            font=dict(color="#ffffff", size=12),
            bgcolor=_ROLLING_AVG_COLOR,
            bordercolor=_ROLLING_AVG_COLOR,
            borderpad=5,
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
    _apply_chart_theme(fig, "Avg Score to Par", height=300)
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
    _apply_chart_theme(fig, "Avg per Round", height=300)
    _add_bar_badges(fig, labels, values, colors, lambda v: f"{v:g}")
    return fig


def _build_analysis_body(points, scoring_profile=None):
    scoring_profile = scoring_profile or {}
    has_putts = any(p.get("putts_rolling_avg") is not None for p in points)
    has_fairway = any(p.get("fairway_rolling_avg") is not None for p in points)
    par_type_fig = _par_type_figure(scoring_profile.get("par_type_breakdown") or [])
    scoring_fig = _scoring_breakdown_figure(scoring_profile.get("scoring_breakdown") or [])

    if not has_putts and not has_fairway and not par_type_fig and not scoring_fig:
        return html.P(
            "No completed rounds with scoring data yet -- play a round and "
            "enter scores as you go to see trends here.",
            className="t3g-empty-state",
        )

    # Both dimensions matter here, not just width: config.responsive=True
    # makes Plotly re-measure its container on every resize event and
    # redraw at that size -- including height. If the container's own
    # CSS height is "auto" (i.e. derived from its content, which *is*
    # the chart), that becomes a feedback loop: the chart measures its
    # box, draws itself that tall, the box grows to fit what it just
    # drew, Plotly sees a resize and measures again, draws taller still
    # -- runaway growth with no ceiling. Giving the dcc.Graph's own style
    # a fixed pixel height (matching the figure's own layout height, set
    # in _apply_chart_theme) breaks the loop: the box's height no longer
    # depends on the chart's rendered size, so there's nothing left for
    # Plotly's resize observer to chase. Width is left as "100%" (a
    # percentage of the *card's* width, not of its own content) so it
    # still shrinks to fit the grid/mobile breakpoint as intended.
    cards = []
    if has_putts:
        cards.append(
            html.Div(
                className="t3g-analysis-card",
                children=[
                    html.H4("Putts per Round", className="t3g-analysis-card-title"),
                    dcc.Graph(
                        figure=_build_figure(points, "putts_total", "putts_rolling_avg", "Putts"),
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
                    html.H4("Fairways Hit", className="t3g-analysis-card-title"),
                    dcc.Graph(
                        figure=_build_figure(
                            points, "fairway_pct", "fairway_rolling_avg", "Fairway Hit %", hover_suffix="%"
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
                    html.H4("Score to Par by Hole Type", className="t3g-analysis-card-title"),
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
                    html.H4("Scoring Breakdown", className="t3g-analysis-card-title"),
                    dcc.Graph(
                        figure=scoring_fig,
                        config=GRAPH_CONFIG,
                        style={"width": "100%", "height": "300px"},
                    ),
                ],
            )
        )
    return html.Div(className="t3g-analysis-grid", children=cards)


def layout(tab=None, **kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="scoring-history-redirect-signin", refresh=True)

    rounds_resp = requests.get(f"{API_BASE_URL}/rounds/player/{player_id}")
    rounds_history = rounds_resp.json() if rounds_resp.status_code == 200 else []

    player_resp = requests.get(f"{API_BASE_URL}/players/{player_id}")
    player = player_resp.json() if player_resp.status_code == 200 else {}
    player_label = player.get("nickname") or player.get("first_name") or "You"
    player_initial = player_label[0].upper() if player_label else "Y"

    analysis_resp = requests.get(f"{API_BASE_URL}/rounds/player/{player_id}/analysis")
    analysis_points = analysis_resp.json() if analysis_resp.status_code == 200 else []

    scoring_profile_resp = requests.get(f"{API_BASE_URL}/rounds/player/{player_id}/scoring-profile")
    scoring_profile = scoring_profile_resp.json() if scoring_profile_resp.status_code == 200 else {}

    tab_styles, tab_classes = _tab_visibility(tab)

    return html.Div(
        className="t3g-page",
        children=[
            dcc.Store(id="scoring-history-store", data=rounds_history),
            dcc.Store(
                id="scoring-history-player-store",
                # player_id (the signed-in viewer, not any particular
                # round's own player_id) rides along here so the delete/
                # scrap/leave callbacks below know who's asking, the same
                # way _round_scorecard_card already needs it to decide
                # which action a given round's button should even be.
                data={"initial": player_initial, "label": player_label, "player_id": player_id},
            ),
            dcc.Store(id="scoring-history-delete-target"),
            _scoring_subnav(tab_classes),
            html.Div(
                id="scoring-tab-panel-rounds",
                style=tab_styles[0],
                children=html.Div(
                    className="t3g-panel",
                    children=[
                        html.Div(
                            className="t3g-panel-navbar",
                            children=html.H3("Rounds History", className="t3g-panel-navbar-title"),
                        ),
                        html.Div(
                            id="scoring-history-list",
                            className="t3g-panel-body",
                        ),
                    ],
                ),
            ),
            # No outer .t3g-panel card here (unlike the Rounds tab above) --
            # a white/shadowed/bordered card around the whole chart grid was
            # just one more box the individual .t3g-analysis-cards had to
            # fit inside, on top of their own card chrome, and its own fixed
            # padding fought the grid's ability to shrink into the mobile
            # single-column breakpoint. The subnav above already marks this
            # as its own section, so the title + chart grid sit directly on
            # the page instead.
            html.Div(
                id="scoring-tab-panel-analysis",
                style=tab_styles[1],
                className="t3g-analysis-tab-panel",
                children=[
                    _build_analysis_body(analysis_points, scoring_profile),
                ],
            ),
            dbc.Modal(
                id="scoring-history-delete-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle(id="scoring-history-delete-modal-title")),
                    dbc.ModalBody(id="scoring-history-delete-modal-body"),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Cancel", id="scoring-history-delete-cancel", color="secondary"),
                            dbc.Button(
                                "Delete",
                                id="scoring-history-delete-confirm",
                                color="danger",
                            ),
                        ]
                    ),
                ],
            ),
        ],
    )


@callback(
    Output("scoring-tab-panel-rounds", "style", allow_duplicate=True),
    Output("scoring-tab-panel-analysis", "style", allow_duplicate=True),
    Output("scoring-tab-rounds-button", "className"),
    Output("scoring-tab-analysis-button", "className"),
    Input("scoring-tab-rounds-button", "n_clicks"),
    Input("scoring-tab-analysis-button", "n_clicks"),
    prevent_initial_call=True,
)
def switch_scoring_tab(rounds_clicks, analysis_clicks):
    active = "analysis" if dash.ctx.triggered_id == "scoring-tab-analysis-button" else "rounds"
    styles, classes = _tab_visibility(active)
    return styles[0], styles[1], classes[0], classes[1]


@callback(
    Output("scoring-history-list", "children"),
    Input("scoring-history-store", "data"),
    State("scoring-history-player-store", "data"),
)
def render_rounds(rounds_history, player_info):
    if not rounds_history:
        return html.P("No rounds recorded yet.", className="t3g-empty-state")

    player_info = player_info or {}
    return html.Div(
        # Its own class rather than the home panel's .t3g-rounds-list --
        # that one caps height and scrolls internally to fit a sidebar
        # panel, but this is the whole page, so it should just scroll
        # naturally with everything visible.
        className="t3g-scoring-history-list",
        children=[
            _round_scorecard_card(
                r, player_info.get("initial", "Y"), player_info.get("label", "You"), player_info.get("player_id")
            )
            for r in rounds_history
        ],
    )


@callback(
    Output("scoring-history-delete-modal", "is_open"),
    Output("scoring-history-delete-modal-title", "children"),
    Output("scoring-history-delete-modal-body", "children"),
    Output("scoring-history-delete-target", "data"),
    Input({"type": "history-round-action", "round_id": ALL, "action": ALL}, "n_clicks"),
    Input("scoring-history-delete-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_delete_modal(action_clicks, cancel_clicks):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "scoring-history-delete-cancel":
        return False, dash.no_update, dash.no_update, None

    if isinstance(triggered_id, dict) and triggered_id.get("type") == "history-round-action":
        if not any(action_clicks):
            # The set of buttons re-rendering also fires this -- only
            # actually open on a real click.
            raise PreventUpdate

        round_id = triggered_id["round_id"]
        action = triggered_id["action"]

        if action == "scrap":
            title = "Scrap this round?"
            body = (
                "This live round and every score entered so far -- for every player in it -- "
                "will be permanently deleted. This can't be undone."
            )
        elif action == "leave":
            title = "Leave this round?"
            body = (
                "You'll be removed from this round and lose access to it, but it'll keep going "
                "for everyone else still in it. This can't be undone."
            )
        else:
            title = "Delete this round?"
            body = "This round and its scorecard will be permanently deleted. This can't be undone."

        return True, title, body, {"round_id": round_id, "action": action}

    raise PreventUpdate


@callback(
    Output("scoring-history-store", "data"),
    Output("scoring-history-delete-modal", "is_open", allow_duplicate=True),
    Input("scoring-history-delete-confirm", "n_clicks"),
    State("scoring-history-delete-target", "data"),
    State("scoring-history-store", "data"),
    State("scoring-history-player-store", "data"),
    prevent_initial_call=True,
)
def confirm_delete(n_clicks, target, rounds_history, player_info):
    if not target or not target.get("round_id"):
        raise PreventUpdate

    round_id = target["round_id"]
    action = target.get("action", "delete")
    player_id = (player_info or {}).get("player_id")

    if action == "leave":
        response = requests.post(f"{API_BASE_URL}/rounds/{round_id}/players/{player_id}/leave")
        ok_statuses = (204, 404)
    else:
        # Both "scrap" and plain "delete" go through the same endpoint --
        # requesting_player_id is what lets the backend enforce creator-
        # only for the scrap-a-live-casual-round case specifically (see
        # delete_round's docstring); it's ignored for every other case
        # this same button can represent, so it's always safe to send.
        response = requests.delete(
            f"{API_BASE_URL}/rounds/{round_id}", params={"requesting_player_id": player_id}
        )
        ok_statuses = (204, 404)

    if response.status_code not in ok_statuses:
        # Leave the list and modal as-is on an unexpected error -- better
        # than silently pretending it worked.
        raise PreventUpdate

    remaining = [r for r in (rounds_history or []) if r["id"] != round_id]
    return remaining, False