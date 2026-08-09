# target path: frontend/src/layouts/navbar.py (full replacement)
from dash import Input, Output, callback, dcc, html
from flask import session

# NOTE: "Courses" and "Scoring History" don't have real pages behind them
# yet — these links will 404 until those pages exist.


def build_navbar():
    return html.Nav(
        className="t3g-navbar",
        children=html.Div(
            className="t3g-navbar-inner",
            children=[
                html.Div(
                    className="t3g-navbar-left",
                    children=[
                        dcc.Link(
                            [
                                html.Span("T", className="t3g-brand-part"),
                                html.Span("3", className="t3g-brand-accent"),
                                html.Span("G", className="t3g-brand-part"),
                            ],
                            href="/",
                            className="t3g-brand",
                        ),
                        html.Div(
                            className="t3g-nav-links",
                            children=[
                                dcc.Link(
                                    "My Account",
                                    href="/my-account",
                                    className="t3g-nav-link",
                                ),
                                dcc.Link(
                                    "Courses",
                                    href="/courses",
                                    className="t3g-nav-link",
                                ),
                                dcc.Link(
                                    "Scoring History",
                                    href="/scoring-history",
                                    className="t3g-nav-link",
                                ),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    className="t3g-navbar-actions",
                    children=[
                        html.Button(
                            "Sign out",
                            id="signout-button",
                            className="t3g-signout-button",
                        ),
                        dcc.Location(id="signout-redirect", refresh=True),
                    ],
                ),
            ],
        ),
    )


@callback(
    Output("signout-redirect", "pathname"),
    Input("signout-button", "n_clicks"),
    prevent_initial_call=True,
)
def handle_signout(n_clicks):
    session.clear()
    return "/signin"