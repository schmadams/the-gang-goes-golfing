# target path: frontend/src/pages/signin.py (full replacement)
import dash
import dash_bootstrap_components as dbc
import requests
from dash import Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL

dash.register_page(__name__, path="/signin", name="Sign In")

# NOTE: email-only, no password. This is intentionally a placeholder until
# real OAuth is wired up — anyone who knows a registered email can sign in
# as that player. Fine for a private trip app among friends during dev,
# not fine once this is exposed more broadly.


def layout():
    return dbc.Container(
        [
            dcc.Location(id="signin-redirect"),
            dbc.Card(
                dbc.CardBody(
                    [
                        html.H3("Sign in", className="mb-4"),
                        dbc.Input(id="email", placeholder="Email", type="email", className="mb-3"),
                        dbc.Button("Sign in", id="signin-button", color="primary", className="w-100"),
                        html.Div(id="signin-error", className="text-danger mt-2"),
                    ]
                ),
                style={"maxWidth": "360px", "margin": "10vh auto"},
            ),
        ]
    )


@callback(
    Output("signin-redirect", "pathname"),
    Output("signin-error", "children"),
    Input("signin-button", "n_clicks"),
    State("email", "value"),
    prevent_initial_call=True,
)
def handle_signin(n_clicks, email):
    if not email:
        return dash.no_update, "Enter an email address."

    response = requests.get(f"{API_BASE_URL}/player-accounts/email/{email}")

    if response.status_code == 404:
        return dash.no_update, "No account found for that email."
    if response.status_code != 200:
        return dash.no_update, "Something went wrong signing in. Try again."

    account = response.json()
    session["logged_in"] = True
    session["player_id"] = account["player_id"]
    session["name"] = account["name"]
    session["email"] = account["email"]

    return "/", ""