import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html
from flask import session

dash.register_page(__name__, path="/signin", name="Sign In")

# TODO: swap for a real user store (hashed passwords, DB-backed) before this goes anywhere real
USERS = {"admin": "password123"}


def layout():
    return dbc.Container(
        [
            dcc.Location(id="signin-redirect"),
            dbc.Card(
                dbc.CardBody(
                    [
                        html.H3("Sign in", className="mb-4"),
                        dbc.Input(id="username", placeholder="Username", type="text", className="mb-2"),
                        dbc.Input(id="password", placeholder="Password", type="password", className="mb-3"),
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
    State("username", "value"),
    State("password", "value"),
    prevent_initial_call=True,
)
def handle_signin(n_clicks, username, password):
    if USERS.get(username) == password:
        session["logged_in"] = True
        session["username"] = username
        return "/", ""
    return dash.no_update, "Invalid username or password."