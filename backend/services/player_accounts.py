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
#
# NOTE: registering always creates a brand-new `players` row. If someone
# was already added to `players` ahead of time (e.g. by a trip organizer)
# and then registers here, they'll end up with a duplicate player record
# rather than being linked to the existing one. Fine for now, worth
# revisiting if that becomes a real problem.


def layout():
    return dbc.Container(
        [
            dcc.Location(id="signin-redirect"),
            dbc.Card(
                dbc.CardBody(
                    [
                        html.H3("Sign in", className="mb-4"),
                        dbc.Input(id="email", placeholder="Email", type="email", className="mb-3"),
                        dbc.Button("Continue", id="continue-button", color="primary", className="w-100"),
                        html.Div(id="signin-error", className="text-danger mt-2"),
                        html.Div(
                            id="register-section",
                            style={"display": "none"},
                            children=[
                                html.Hr(),
                                html.H5("No account found for that email"),
                                html.P("Create one below to get started.", className="text-muted"),
                                dbc.Input(
                                    id="register-first-name",
                                    placeholder="First name",
                                    className="mb-2",
                                ),
                                dbc.Input(
                                    id="register-surname",
                                    placeholder="Surname",
                                    className="mb-2",
                                ),
                                dbc.Button(
                                    "Create account",
                                    id="register-button",
                                    color="success",
                                    className="w-100",
                                ),
                                html.Div(id="register-error", className="text-danger mt-2"),
                            ],
                        ),
                    ]
                ),
                style={"maxWidth": "360px", "margin": "10vh auto"},
            ),
        ]
    )


def _log_in(account: dict) -> None:
    session["logged_in"] = True
    session["player_id"] = account["player_id"]
    session["name"] = account["name"]
    session["email"] = account["email"]


@callback(
    Output("signin-redirect", "pathname"),
    Output("signin-error", "children"),
    Output("register-section", "style"),
    Output("register-error", "children"),
    Input("continue-button", "n_clicks"),
    Input("register-button", "n_clicks"),
    State("email", "value"),
    State("register-first-name", "value"),
    State("register-surname", "value"),
    prevent_initial_call=True,
)
def handle_signin_or_register(continue_clicks, register_clicks, email, first_name, surname):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "continue-button":
        if not email:
            return dash.no_update, "Enter an email address.", {"display": "none"}, ""

        response = requests.get(f"{API_BASE_URL}/player-accounts/email/{email}")

        if response.status_code == 200:
            _log_in(response.json())
            return "/", "", {"display": "none"}, ""

        if response.status_code == 404:
            return dash.no_update, "", {"display": "block"}, ""

        return dash.no_update, "Something went wrong signing in. Try again.", {"display": "none"}, ""

    if triggered_id == "register-button":
        if not email:
            return dash.no_update, "Enter an email address above first.", {"display": "block"}, ""

        if not first_name or not surname:
            return dash.no_update, dash.no_update, {"display": "block"}, "Enter a first name and surname."

        player_resp = requests.post(
            f"{API_BASE_URL}/players/",
            json={"first_name": first_name, "surname": surname},
        )
        if player_resp.status_code != 201:
            return (
                dash.no_update,
                dash.no_update,
                {"display": "block"},
                "Couldn't create a player record. Try again.",
            )

        player = player_resp.json()

        account_resp = requests.post(
            f"{API_BASE_URL}/player-accounts/",
            json={
                "email": email,
                "name": f"{first_name} {surname}",
                "player_id": player["id"],
            },
        )
        if account_resp.status_code == 409:
            return (
                dash.no_update,
                dash.no_update,
                {"display": "block"},
                "An account already exists for that email. Try Continue again.",
            )
        if account_resp.status_code != 201:
            return (
                dash.no_update,
                dash.no_update,
                {"display": "block"},
                "Couldn't create an account. Try again.",
            )

        _log_in(account_resp.json())
        return "/", "", {"display": "none"}, ""

    return dash.no_update, dash.no_update, dash.no_update, dash.no_update