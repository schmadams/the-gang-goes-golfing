# target path: frontend/src/pages/analysis.py (new file)
import dash
import plotly.graph_objects as go
import requests
from dash import dcc, html
from flask import session

from config import API_BASE_URL

dash.register_page(__name__, path="/analysis", name="Player Analysis")

_ROLLING_AVG_COLOR = "#c21861"
_RAW_POINT_COLOR = "#c7cad1"


def _build_figure(points, raw_field, avg_field, y_title, hover_suffix=""):
    # Only plot rounds where this particular stat has a rolling average --
    # that's exactly the subset the backend computed the average over, so
    # a round missing putts data (say) doesn't show up as a gap or a zero.
    filtered = [p for p in points if p.get(avg_field) is not None]
    dates = [p["date"] for p in filtered]
    raw_values = [p[raw_field] for p in filtered]
    avg_values = [p[avg_field] for p in filtered]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates,
        y=raw_values,
        mode="markers",
        name="Per round",
        marker=dict(color=_RAW_POINT_COLOR, size=7),
        hovertemplate=f"%{{x}}<br>%{{y}}{hover_suffix}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=dates,
        y=avg_values,
        mode="lines+markers",
        name="5-round rolling avg",
        line=dict(color=_ROLLING_AVG_COLOR, width=3),
        marker=dict(color=_ROLLING_AVG_COLOR, size=6),
        hovertemplate=f"%{{x}}<br>%{{y}}{hover_suffix} avg<extra></extra>",
    ))
    fig.update_layout(
        margin=dict(l=45, r=20, t=10, b=40),
        height=340,
        yaxis_title=y_title,
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        font=dict(color="#1e2a47"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0f1f5", zeroline=False)
    return fig


def layout(**kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="analysis-redirect-signin", refresh=True)

    response = requests.get(f"{API_BASE_URL}/rounds/player/{player_id}/analysis")
    points = response.json() if response.status_code == 200 else []

    has_putts = any(p.get("putts_rolling_avg") is not None for p in points)
    has_fairway = any(p.get("fairway_rolling_avg") is not None for p in points)

    if not has_putts and not has_fairway:
        body = html.P(
            "No completed rounds with putts or fairway hit data yet -- play a round "
            "and enter putts/fairway hit as you go to see trends here.",
            className="t3g-empty-state",
        )
    else:
        cards = []
        if has_putts:
            cards.append(
                html.Div(
                    className="t3g-analysis-card",
                    children=[
                        html.H4("Putts per Round", className="t3g-analysis-card-title"),
                        dcc.Graph(
                            figure=_build_figure(points, "putts_total", "putts_rolling_avg", "Putts"),
                            config={"displayModeBar": False},
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
                            config={"displayModeBar": False},
                        ),
                    ],
                )
            )
        body = html.Div(className="t3g-analysis-grid", children=cards)

    return html.Div(
        className="t3g-page",
        children=html.Div(
            className="t3g-panel",
            children=[
                html.Div(
                    className="t3g-panel-navbar",
                    children=html.H3("Player Analysis", className="t3g-panel-navbar-title"),
                ),
                html.Div(body, className="t3g-panel-body"),
            ],
        ),
    )