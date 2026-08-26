# target path: frontend/src/pages/club.py (replace entire file)
import base64
import time

import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import requests
from dash import ALL, MATCH, Input, Output, State, callback, dcc, html
from flask import session

from components.scorecard import live_badge
from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path_template="/clubs/<slug>", name="Club")

# Player Comparison charts (below) are read-only Plotly config, same as
# analysis.py/profile.py's GRAPH_CONFIG -- no modebar clutter, still
# responsive to its container so it doesn't overflow on mobile.
_GRAPH_CONFIG = {"displayModeBar": False, "responsive": True}

# One color per player, cycled by index (players sorted by name, same
# order the backend already returns them in) -- kept distinct from each
# other rather than matched to brand colors, since this chart's whole
# point is telling players apart at a glance. Reused across every one of
# the 6 comparison charts below so a given player is always the same
# color no matter which chart you're looking at.
_PLAYER_COLORS = [
    "#1e2a47", "#c21861", "#1f9d55", "#e2a80a",
    "#4062bb", "#a45ee5", "#e0623c", "#0e9594",
]

_SCORING_CATEGORY_ORDER = ["birdie_or_better", "par", "bogey", "double_bogey_plus"]
_SCORING_CATEGORY_LABELS = {
    "birdie_or_better": "Birdie+",
    "par": "Par",
    "bogey": "Bogey",
    "double_bogey_plus": "Double+",
}

_DISTANCE_BIN_ORDER = ["< 150y", "150-249y", "250-349y", "350-449y", "450y+"]


def _apply_comparison_chart_theme(fig, height=300):
    """Same minimal-axis look as analysis.py/profile.py's _apply_chart_theme
    (right-side dashed gridlines, muted small tick labels, transparent
    background, no legend) -- players are told apart by color plus the
    player name baked into each trace's hovertemplate, same as every
    other chart in this app relying on hover rather than a legend."""
    fig.update_layout(
        autosize=True,
        margin=dict(l=8, r=40, t=8, b=32),
        height=height,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#1e2a47"),
        showlegend=False,
        hoverlabel=dict(bgcolor="#1e2a47", bordercolor="#1e2a47", font=dict(color="#ffffff")),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, tickfont=dict(size=10, color="#9aa0b0"))
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


def _leaderboard_initials(name):
    """Same initials rule as tournament.py's own leaderboard avatars --
    duplicated here rather than imported since club.py and tournament.py
    don't share a components module for this."""
    words = [w for w in (name or "").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def _leaderboard_avatar(name, photo_url):
    """The circle at the start of every leaderboard row -- the player's
    real profile picture once they've uploaded one (photo_url comes
    straight through from get_club_player_comparison, which now carries
    it off the same players(*) embed list_players_in_club already
    fetches), falling back to the initials badge otherwise exactly like
    before. Same t3g-leaderboard-avatar sizing/circle either way -- the
    photo variant just adds --photo for object-fit: cover (see club.css)
    so a non-square upload doesn't stretch."""
    if photo_url:
        return html.Img(
            src=photo_url,
            alt="",
            className="t3g-leaderboard-avatar t3g-leaderboard-avatar--photo",
        )
    return html.Span(_leaderboard_initials(name), className="t3g-leaderboard-avatar")


def _leaderboard_positions(values):
    """Sequential rank with ties sharing a position (T-prefixed once they
    do) -- same convention tournament.py's own leaderboard uses. Expects
    `values` already sorted best-to-worst."""
    positions = []
    rank = 0
    prev = object()  # sentinel -- never equal to a real value, so row 0 always ranks 1
    for i, v in enumerate(values):
        if v != prev:
            rank = i + 1
        positions.append(rank)
        prev = v

    counts: dict[int, int] = {}
    for p in positions:
        counts[p] = counts.get(p, 0) + 1
    return [f"T{p}" if counts[p] > 1 else str(p) for p in positions]


def _stat_leaderboard(players, series_by_player, value_field, ascending, value_suffix="", decimals=1):
    """Ranks players by their average value_field across this club's
    qualifying rounds -- ascending=True for stats where lower is better
    (Putts per round), False where higher is better (Fairways Hit %).
    Averages the raw per-round values directly rather than reading the
    already-smoothed *_rolling_avg fields the comparison payload also
    carries (those exist for the Scoring History trend line, not for a
    single season-to-date standing) -- a straight average across every
    qualifying round is the number a leaderboard actually needs.
    Reuses the same table chrome (t3g-leaderboard-table) as the
    tournament leaderboard, just Pos/Player/Avg/Rounds columns instead
    of a full scorecard grid."""
    rows = []
    for player in players:
        pid = player["player_id"]
        values = [p[value_field] for p in series_by_player.get(pid, []) if p.get(value_field) is not None]
        if not values:
            continue
        rows.append((player, round(sum(values) / len(values), decimals), len(values)))

    if not rows:
        return None

    rows.sort(key=lambda r: r[1], reverse=not ascending)
    positions = _leaderboard_positions([r[1] for r in rows])

    header_row = html.Tr([html.Th(""), html.Th("Player"), html.Th("Avg"), html.Th("Rounds")])
    body_rows = []
    for pos, (player, avg_value, rounds_played) in zip(positions, rows):
        tier = int(pos.lstrip("T"))
        tier_class = {1: " t3g-leaderboard-pos-badge--first", 2: " t3g-leaderboard-pos-badge--second", 3: " t3g-leaderboard-pos-badge--third"}.get(tier, "")
        row_class = "t3g-leaderboard-row--leader" if tier == 1 else ""
        body_rows.append(
            html.Tr(
                [
                    html.Td(html.Span(pos, className="t3g-leaderboard-pos-badge" + tier_class), className="t3g-leaderboard-pos"),
                    html.Td(
                        html.Div(
                            [
                                _leaderboard_avatar(player["name"], player.get("photo_url")),
                                html.Span(player["name"]),
                            ],
                            className="t3g-leaderboard-player-cell",
                        ),
                        className="t3g-leaderboard-player-col",
                    ),
                    html.Td(f"{avg_value:g}{value_suffix}"),
                    html.Td(str(rounds_played)),
                ],
                className=row_class,
            )
        )

    return html.Div(
        html.Table(
            [html.Thead(header_row), html.Tbody(body_rows)],
            className="t3g-leaderboard-table t3g-leaderboard-table--simple",
        ),
        className="t3g-leaderboard-wrap",
    )


def _category_scatter_figure(players, series_by_player, category_key, category_order, category_labels, value_key, height=300):
    """One marker per player per category (hole type / scoring bucket /
    distance bin) rather than grouped bars -- with up to 8 players on one
    chart, grouped bars per category get cramped fast, while a scatter
    just needs color to tell players apart and reads fine even fairly
    dense. Skips any category a player has no value for (rather than
    plotting a 0, which would misread as "actually scored this well")."""
    fig = go.Figure()
    ordered_labels = [category_labels.get(c, c) for c in category_order]

    for i, player in enumerate(players):
        pid = player["player_id"]
        by_category = {row[category_key]: row.get(value_key) for row in series_by_player.get(pid, [])}
        xs = [category_labels.get(c, c) for c in category_order if by_category.get(c) is not None]
        ys = [by_category[c] for c in category_order if by_category.get(c) is not None]
        if not ys:
            continue
        color = _PLAYER_COLORS[i % len(_PLAYER_COLORS)]
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="markers",
            name=player["name"],
            marker=dict(color=color, size=10, line=dict(color="#ffffff", width=1.5)),
            hovertemplate=f"{player['name']}<br>%{{x}}: %{{y}}<extra></extra>",
        ))

    if not fig.data:
        return None

    _apply_comparison_chart_theme(fig, height=height)
    fig.update_xaxes(categoryorder="array", categoryarray=ordered_labels)
    return fig


_PAR_TYPE_TABS = [(3, "Par 3"), (4, "Par 4"), (5, "Par 5")]
_DEFAULT_PAR_TYPE_TAB = 4


def _to_par_text(value):
    """Same E/+N/-N convention tournament.py's own leaderboard uses for a
    to-par number, just fed a float (avg_score_to_par can be a fractional
    average across several rounds, e.g. +0.8) instead of an integer round
    total."""
    if value is None:
        return "--"
    if value == 0:
        return "E"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:g}"


def _par_type_tab_classes(active_par):
    return {
        par: (
            "t3g-leaderboard-format-tab t3g-leaderboard-format-tab--active"
            if par == active_par
            else "t3g-leaderboard-format-tab"
        )
        for par, _label in _PAR_TYPE_TABS
    }


def _par_type_leaderboard(players, par_type_by_player, par):
    """Same leaderboard chrome as _stat_leaderboard, but pivoted on one
    hole type (par 3/4/5) at a time out of the par_type series each
    player already carries, rather than one flat per-player average --
    that's what the Par 3/4/5 tabs switch between. Ranked ascending
    (closer to -- or under -- par first), same direction golf leaderboards
    already sort by."""
    rows = []
    for player in players:
        pid = player["player_id"]
        entry = next((r for r in par_type_by_player.get(pid, []) if r.get("par") == par), None)
        if not entry or entry.get("avg_score_to_par") is None:
            continue
        rows.append((player, entry["avg_score_to_par"]))

    if not rows:
        return None

    rows.sort(key=lambda r: r[1])
    positions = _leaderboard_positions([r[1] for r in rows])

    header_row = html.Tr([html.Th(""), html.Th("Player"), html.Th("Avg to Par")])
    body_rows = []
    for pos, (player, avg_value) in zip(positions, rows):
        tier = int(pos.lstrip("T"))
        tier_class = {1: " t3g-leaderboard-pos-badge--first", 2: " t3g-leaderboard-pos-badge--second", 3: " t3g-leaderboard-pos-badge--third"}.get(tier, "")
        row_class = "t3g-leaderboard-row--leader" if tier == 1 else ""
        body_rows.append(
            html.Tr(
                [
                    html.Td(html.Span(pos, className="t3g-leaderboard-pos-badge" + tier_class), className="t3g-leaderboard-pos"),
                    html.Td(
                        html.Div(
                            [
                                _leaderboard_avatar(player["name"], player.get("photo_url")),
                                html.Span(player["name"]),
                            ],
                            className="t3g-leaderboard-player-cell",
                        ),
                        className="t3g-leaderboard-player-col",
                    ),
                    html.Td(_to_par_text(avg_value)),
                ],
                className=row_class,
            )
        )

    return html.Div(
        html.Table(
            [html.Thead(header_row), html.Tbody(body_rows)],
            className="t3g-leaderboard-table t3g-leaderboard-table--simple",
        ),
        className="t3g-leaderboard-wrap",
    )


def _par_type_leaderboard_card(players, par_type_by_player):
    """Score to Par by Hole Type -- Par 3/4/5 tab bar (same pill styling
    as the tournament Leaderboard's Gross/Stableford/Nett tabs) over a
    ranked table for whichever hole type is selected, defaulting to
    Par 4. switch_club_par_type_tab (below) swaps the table and active
    tab on click without a server round-trip -- the whole per-player,
    per-par breakdown is small enough to ship down once in a Store."""
    tab_classes = _par_type_tab_classes(_DEFAULT_PAR_TYPE_TAB)
    table = _par_type_leaderboard(players, par_type_by_player, _DEFAULT_PAR_TYPE_TAB)

    return html.Div(
        className="t3g-analysis-card",
        children=[
            html.H4("Score to Par by Hole Type", className="t3g-analysis-card-title"),
            dcc.Store(id="club-partype-store", data=par_type_by_player),
            dcc.Store(id="club-partype-players-store", data=players),
            html.Div(
                className="t3g-leaderboard-format-tabs mb-2",
                children=[
                    html.Button(label, id=f"club-partype-tab-{par}", className=tab_classes[par], n_clicks=0)
                    for par, label in _PAR_TYPE_TABS
                ],
            ),
            html.Div(
                id="club-partype-table",
                children=table if table is not None else html.P(
                    "No par-3/4/5 breakdown available yet -- needs holes with a known par.",
                    className="t3g-empty-state",
                ),
            ),
        ],
    )


@callback(
    Output("club-partype-table", "children"),
    Output("club-partype-tab-3", "className"),
    Output("club-partype-tab-4", "className"),
    Output("club-partype-tab-5", "className"),
    Input("club-partype-tab-3", "n_clicks"),
    Input("club-partype-tab-4", "n_clicks"),
    Input("club-partype-tab-5", "n_clicks"),
    State("club-partype-store", "data"),
    State("club-partype-players-store", "data"),
    prevent_initial_call=True,
)
def switch_club_par_type_tab(clicks_3, clicks_4, clicks_5, par_type_by_player, players):
    triggered_id = dash.ctx.triggered_id
    par = {"club-partype-tab-3": 3, "club-partype-tab-4": 4, "club-partype-tab-5": 5}.get(
        triggered_id, _DEFAULT_PAR_TYPE_TAB
    )
    table = _par_type_leaderboard(players or [], par_type_by_player or {}, par)
    if table is None:
        table = html.P(f"No Par {par} data yet for this club's rounds.", className="t3g-empty-state")
    classes = _par_type_tab_classes(par)
    return table, classes[3], classes[4], classes[5]

def _club_comparison_panel(comparison):
    """Player Analysis tab body -- the club-scoped, multi-player sibling
    of the Player Analysis charts on analysis.py/profile.py. Only rounds
    tied to this specific club count (its own tournament rounds, or a
    casual round explicitly tagged with it -- see get_club_player_
    comparison in backend/services/rounds.py), and only members with at
    least one qualifying round show up at all.

    No outer .t3g-panel/build_panel_navbar chrome -- now that this lives
    on its own subnav tab (see _club_subnav), that card-around-the-cards
    wrapper is redundant the same way it was on analysis.py's own
    Analysis tab (t3g-analysis-tab-panel replaced it there too)."""
    players = comparison.get("players") or []

    if not players:
        return html.Div(
            className="t3g-analysis-tab-panel",
            children=html.P(
                "No club rounds recorded yet -- once members play a club "
                "tournament round, or tag a casual round with this club when "
                "starting it, their stats will show up here side by side.",
                className="t3g-empty-state",
            ),
        )

    # Putts and Fairway are leaderboard tables (ranked average, best to
    # worst) rather than charts -- a season-standing "who's best" reads
    # more naturally as a ranked list than as lines on a graph, and it
    # sidesteps needing a legend to tell players apart. The remaining
    # three chart types don't have as natural a single ranking number
    # (a strokes-per-round trend, or a breakdown across several
    # categories at once), so they stay as charts.
    #
    # Every one of the 6 stats always gets its own card, even when there's
    # nothing to show -- a stat with no qualifying data (e.g. fairway_hit
    # was never recorded on any of this club's rounds, even though putts
    # and strokes were) used to just silently vanish from the grid, which
    # read as "broken" rather than "no data yet". An explicit empty-state
    # message per card makes that distinction visible instead of leaving
    # a gap the same as the "no players at all" case above.
    table_specs = [
        (
            "Putts per Round",
            _stat_leaderboard(players, comparison.get("putts") or {}, "putts_total", ascending=True),
            "No putts recorded yet for this club's rounds.",
        ),
        (
            "Fairways Hit %",
            _stat_leaderboard(players, comparison.get("fairway") or {}, "fairway_pct", ascending=False, value_suffix="%"),
            "No fairway-hit data recorded yet for this club's rounds.",
        ),
        (
            "Scoring History",
            _stat_leaderboard(players, comparison.get("scoring_history") or {}, "total_strokes", ascending=True),
            "No completed scorecards yet for this club's rounds.",
        ),
    ]

    chart_specs = [
        (
            "Scoring Breakdown (avg per round)",
            _category_scatter_figure(
                players, comparison.get("scoring_breakdown") or {}, "category",
                _SCORING_CATEGORY_ORDER, _SCORING_CATEGORY_LABELS, "avg_per_round",
            ),
            "No scoring breakdown available yet for this club's rounds.",
        ),
        (
            "Avg Shots by Hole Distance",
            _category_scatter_figure(
                players, comparison.get("distance_profile") or {}, "bin", _DISTANCE_BIN_ORDER,
                {label: label for label in _DISTANCE_BIN_ORDER}, "avg_strokes",
            ),
            "No hole yardage data available yet for this club's rounds.",
        ),
    ]

    cards = [
        html.Div(
            className="t3g-analysis-card",
            children=[
                html.H4(title, className="t3g-analysis-card-title"),
                table if table is not None else html.P(empty_message, className="t3g-empty-state"),
            ],
        )
        for title, table, empty_message in table_specs
    ] + [
        # Score to Par by Hole Type is its own tabbed leaderboard card
        # (Par 3/4/5 tabs, see _par_type_leaderboard_card) rather than a
        # plain (title, component, empty_message) tuple in the list above
        # -- it needs its own Store + tab bar + swappable table body, the
        # same reason profile.py's Scoring History card was always built
        # inline instead of going through its generic per-page loop.
        _par_type_leaderboard_card(players, comparison.get("par_type") or {}),
    ] + [
        html.Div(
            className="t3g-analysis-card",
            children=[
                html.H4(title, className="t3g-analysis-card-title"),
                (
                    dcc.Graph(figure=fig, config=_GRAPH_CONFIG, style={"width": "100%", "height": "340px"})
                    if fig is not None
                    else html.P(empty_message, className="t3g-empty-state")
                ),
            ],
        )
        for title, fig, empty_message in chart_specs
    ]

    return html.Div(
        className="t3g-analysis-tab-panel",
        children=(
            html.Div(className="t3g-analysis-grid", children=cards)
            if cards
            else html.P("Not enough data yet to compare players.", className="t3g-empty-state")
        ),
    )

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


def _club_photo_panel(club, is_admin):
    """Admin-only club photo upload -- same dcc.Upload -> base64-decode ->
    POST multipart -> reload pattern as my_account.py's own Profile
    Picture panel, just POSTing to /clubs/{id}/photo (which checks
    admin_id server-side, see upload_club_photo's admin gate) instead of
    /players/{id}/profile-picture. Renders nothing for non-admins, same
    as _invite_panel just above -- the photo itself (once uploaded) is
    public and already shows up wherever club.get("photo_url") is read
    (home.py's clubs grid, frontend/src/pages/clubs.py's index), this
    panel is only the upload control."""
    if not is_admin:
        return None

    photo_url = club.get("photo_url")
    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Club Photo"),
            html.Div(
                className="t3g-panel-body t3g-photo-panel-body",
                children=[
                    html.Img(
                        id="club-photo-preview",
                        src=photo_url or "",
                        className="t3g-profile-photo",
                        style={} if photo_url else {"display": "none"},
                    ),
                    dcc.Upload(
                        id="club-photo-upload",
                        children=html.Button(
                            "Upload Photo", className="t3g-panel-action-button"
                        ),
                        accept="image/*",
                        style={"display": "inline-block"},
                    ),
                    html.Div(id="club-photo-error", className="text-danger mt-2"),
                    dcc.Location(id="club-photo-redirect", refresh=True),
                ],
            ),
        ],
    )


def _admin_tab_panel(club, player_id, is_admin):
    """Everything admin-only on the club page lives here now, instead of
    scattered across whichever tab it happened to relate to (Club Photo
    and Invite a Player used to sit at the top of Directory, Create
    Tournament used to sit in the Tournaments tab's own navbar). One
    place a club admin goes for every management action, and nothing for
    anyone else to stumble onto.

    Each of the three panels below still privately re-checks is_admin/
    player_id itself (see _invite_panel/_club_photo_panel's own guards)
    as defense in depth, in case a non-admin somehow lands on ?tab=admin
    directly -- _club_tab_visibility already redirects that case back to
    Directory, but this is a second layer that costs nothing."""
    if not is_admin:
        return None

    return html.Div(
        children=[
            _club_photo_panel(club, is_admin),
            _invite_panel(club, player_id),
            _tournament_admin_card(),
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


def _tournaments_panel(tournaments, slug):
    """No admin action here anymore -- Create Tournament used to live in
    this panel's navbar, but every admin-only action on the club page now
    lives together under the Admin tab (see _tournament_admin_card /
    _admin_tab_panel) instead of being scattered across whichever tab it
    happens to relate to. This panel is now the same for every viewer."""
    body = (
        html.Div([_tournament_item(t, slug) for t in tournaments], className="t3g-tournament-list")
        if tournaments
        else html.P("No tournaments yet.", className="t3g-empty-state")
    )

    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Tournaments"),
            html.Div(body, className="t3g-panel-body"),
        ],
    )


def _tournament_admin_card():
    """Create Tournament trigger, relocated here from the Tournaments
    tab's own navbar action -- still opens the exact same _tournament_
    modal() (that modal isn't tab-scoped, it's always in the DOM
    regardless of which tab is active) via the same tournament-create-
    button id handle_tournament_modal already listens on. Only the
    trigger moved, not the create flow itself -- after a tournament is
    created the page reloads and it shows up on the Tournaments tab as
    normal."""
    return html.Div(
        className="t3g-panel",
        children=[
            build_panel_navbar("Tournaments"),
            html.Div(
                className="t3g-panel-body",
                children=[
                    html.P(
                        "Start a new competition for this club.",
                        className="t3g-empty-state mb-2",
                    ),
                    html.Button(
                        "Create Tournament",
                        id="tournament-create-button",
                        className="t3g-panel-action-button",
                        n_clicks=0,
                    ),
                ],
            ),
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


_CLUB_TAB_KEYS = ("directory", "tournaments", "comparison", "admin")
_CLUB_TAB_BUTTON_BASE = "t3g-tournament-tab"
_CLUB_TAB_BUTTON_ACTIVE = "t3g-tournament-tab t3g-tournament-tab--active"


def _club_tab_visibility(active_tab, is_admin):
    """(styles, classes) for the club page's tabs at page-load time --
    same ?tab= query param pattern tournament.py's own subnav uses (see
    _tab_visibility there), picked once at load instead of always
    defaulting to Directory, so a link elsewhere in the app can open
    straight onto Tournaments/Player Analysis/Admin. switch_club_tab
    below owns in-page click-driven switching after that.

    A non-admin asking for ?tab=admin falls back to Directory instead of
    landing on a tab whose button they can't even see (see _club_subnav
    -- the Admin button stays in the DOM but hidden via style for
    non-admins, purely so switch_club_tab's Output targets always
    exist)."""
    hidden = {"display": "none"}
    shown = {}
    requested = active_tab if active_tab in _CLUB_TAB_KEYS else "directory"
    key = requested if requested != "admin" or is_admin else "directory"
    index = _CLUB_TAB_KEYS.index(key)

    styles = tuple(shown if i == index else hidden for i in range(len(_CLUB_TAB_KEYS)))
    classes = tuple(
        _CLUB_TAB_BUTTON_ACTIVE if i == index else _CLUB_TAB_BUTTON_BASE for i in range(len(_CLUB_TAB_KEYS))
    )
    return styles, classes


def _club_subnav(tab_classes, is_admin):
    """Page-level subnav for the club page -- Directory/Tournaments/
    Player Analysis/Admin as client-side tabs, every panel group always
    in the DOM and toggled by style (see switch_club_tab below), same
    approach and same .t3g-tournament-subnav/-tabs/-tab styling as
    tournament.py's own subnav. No "Return to X" link here -- unlike the
    tournament page, this already is the club's own top-level page.

    The Admin button itself is always rendered (never omitted from the
    tree) even for non-admins -- just hidden via style, the same "always
    in DOM, toggle via style" convention every tab panel here already
    uses. That's specifically so switch_club_tab's className Output for
    this button always has a real target to write to; if the button were
    omitted outright for non-admins, a non-admin clicking any other tab
    would trip a Dash error trying to update a button that doesn't
    exist."""
    directory_class, tournaments_class, comparison_class, admin_class = tab_classes
    return html.Div(
        className="t3g-tournament-subnav",
        children=html.Div(
            className="t3g-tournament-subnav-inner",
            children=html.Div(
                className="t3g-tournament-tabs",
                children=[
                    html.Button(
                        "Directory",
                        id="club-tab-directory-button",
                        className=directory_class,
                        n_clicks=0,
                    ),
                    html.Button(
                        "Tournaments",
                        id="club-tab-tournaments-button",
                        className=tournaments_class,
                        n_clicks=0,
                    ),
                    html.Button(
                        "Player Analysis",
                        id="club-tab-comparison-button",
                        className=comparison_class,
                        n_clicks=0,
                    ),
                    html.Button(
                        "Admin",
                        id="club-tab-admin-button",
                        className=admin_class,
                        n_clicks=0,
                        style={} if is_admin else {"display": "none"},
                    ),
                ],
            ),
        ),
    )


@callback(
    Output("club-tab-panel-directory", "style"),
    Output("club-tab-panel-tournaments", "style"),
    Output("club-tab-panel-comparison", "style"),
    Output("club-tab-panel-admin", "style"),
    Output("club-tab-directory-button", "className"),
    Output("club-tab-tournaments-button", "className"),
    Output("club-tab-comparison-button", "className"),
    Output("club-tab-admin-button", "className"),
    Input("club-tab-directory-button", "n_clicks"),
    Input("club-tab-tournaments-button", "n_clicks"),
    Input("club-tab-comparison-button", "n_clicks"),
    Input("club-tab-admin-button", "n_clicks"),
    prevent_initial_call=True,
)
def switch_club_tab(directory_clicks, tournaments_clicks, comparison_clicks, admin_clicks):
    # A non-admin can never actually trigger the "admin" branch below --
    # their Admin button is display:none (see _club_subnav), so it can't
    # be clicked -- but the branch still needs to exist since this same
    # callback (and its Outputs) are shared by every viewer regardless of
    # role.
    hidden = {"display": "none"}
    shown = {}
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "club-tab-tournaments-button":
        return (
            hidden, shown, hidden, hidden,
            _CLUB_TAB_BUTTON_BASE, _CLUB_TAB_BUTTON_ACTIVE, _CLUB_TAB_BUTTON_BASE, _CLUB_TAB_BUTTON_BASE,
        )
    if triggered_id == "club-tab-comparison-button":
        return (
            hidden, hidden, shown, hidden,
            _CLUB_TAB_BUTTON_BASE, _CLUB_TAB_BUTTON_BASE, _CLUB_TAB_BUTTON_ACTIVE, _CLUB_TAB_BUTTON_BASE,
        )
    if triggered_id == "club-tab-admin-button":
        return (
            hidden, hidden, hidden, shown,
            _CLUB_TAB_BUTTON_BASE, _CLUB_TAB_BUTTON_BASE, _CLUB_TAB_BUTTON_BASE, _CLUB_TAB_BUTTON_ACTIVE,
        )
    return (
        shown, hidden, hidden, hidden,
        _CLUB_TAB_BUTTON_ACTIVE, _CLUB_TAB_BUTTON_BASE, _CLUB_TAB_BUTTON_BASE, _CLUB_TAB_BUTTON_BASE,
    )


def layout(slug=None, tab=None, **kwargs):
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

    comparison_resp = requests.get(f"{API_BASE_URL}/clubs/{club['id']}/player-comparison")
    comparison = comparison_resp.json() if comparison_resp.status_code == 200 else {}

    (directory_style, tournaments_style, comparison_style, admin_style), tab_classes = _club_tab_visibility(
        tab, is_admin
    )

    return html.Div(
        # t3g-club-page scopes the more compact panel spacing in club.css
        # -- .t3g-panel/-navbar/-body are shared with every other page, so
        # those overrides are kept local to this one rather than tightening
        # spacing app-wide.
        className="t3g-page t3g-club-page",
        children=[
            dcc.Store(id="club-id-store", data=club["id"]),
            _admin_banner(is_admin),
            _club_subnav(tab_classes, is_admin),
            html.Div(
                id="club-tab-panel-directory",
                style=directory_style,
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
                ],
            ),
            html.Div(
                id="club-tab-panel-tournaments",
                style=tournaments_style,
                children=_tournaments_panel(tournaments, slug),
            ),
            html.Div(
                id="club-tab-panel-comparison",
                style=comparison_style,
                children=_club_comparison_panel(comparison),
            ),
            html.Div(
                id="club-tab-panel-admin",
                style=admin_style,
                children=_admin_tab_panel(club, player_id, is_admin),
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
    Output("club-photo-error", "children"),
    Output("club-photo-redirect", "href"),
    Input("club-photo-upload", "contents"),
    State("club-photo-upload", "filename"),
    State("club-id-store", "data"),
    State("_pages_location", "pathname"),
    prevent_initial_call=True,
)
def handle_club_photo_upload(contents, filename, club_id, current_pathname):
    if not contents:
        return "", dash.no_update

    player_id = session.get("player_id")

    header, encoded = contents.split(",", 1)
    file_bytes = base64.b64decode(encoded)
    content_type = header.split(";")[0].replace("data:", "") or "image/jpeg"

    response = requests.post(
        f"{API_BASE_URL}/clubs/{club_id}/photo",
        data={"admin_id": player_id},
        files={"file": (filename or "photo.jpg", file_bytes, content_type)},
    )

    if response.status_code != 200:
        # Same "surface the real backend detail" treatment as
        # my_account.py's handle_photo_upload -- upload_club_photo_route
        # returns a specific message via ImageUploadError for a Supabase
        # Storage failure (e.g. a missing bucket), or NotClubAdminError/
        # ClubNotFoundError for the other failure paths, rather than
        # always showing the same generic line.
        try:
            detail = response.json().get("detail", "Couldn't upload that photo. Try again.")
            if not isinstance(detail, str):
                detail = "Couldn't upload that photo. Try again."
        except ValueError:
            detail = "Couldn't upload that photo. Try again."
        return detail, dash.no_update

    # Same cache-busting reload as create_tournament_submit and the rest
    # of this file's own redirect-on-success callbacks (see the comment
    # there) -- dcc.Location only reloads when the value differs from
    # what's already loaded, and the pathname itself doesn't change here.
    return "", f"{current_pathname}?_r={time.time()}"


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