# target path: frontend/src/pages/scoring_history.py (full replacement)
"""
Scoring History used to be its own page -- it's now the "Rounds" tab of
the merged Analysis page (pages/analysis.py), reusing the tournament-style
subnav. This file stays registered at the old /scoring-history path purely
so an existing bookmark or link still lands somewhere useful, redirecting
straight to the Rounds tab of the merged page instead of 404ing.
"""
import dash
from dash import dcc

dash.register_page(__name__, path="/scoring-history", name="Rounds History")


def layout(**kwargs):
    return dcc.Location(pathname="/analysis", search="?tab=rounds", id="scoring-history-redirect", refresh=True)