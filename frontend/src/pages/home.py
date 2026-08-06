import dash
from dash import Input, Output, callback, dcc, html
from flask import session

dash.register_page(__name__, path="/", name="Home")


def layout():
    if not session.get("logged_in"):
        # Not signed in — bounce to the sign-in page
        return dcc.Location(pathname="/signin", id="redirect-to-signin")

    return html.Div(
        [
            html.H2(f"Welcome, {session.get('username')}"),
            html.P("Home page content here."),
            html.Button("Sign out", id="signout-button"),
            dcc.Location(id="signout-redirect"),
        ],
        style={"color": "white", "padding": "2rem"},
    )


@callback(
    Output("signout-redirect", "pathname"),
    Input("signout-button", "n_clicks"),
    prevent_initial_call=True,
)
def handle_signout(n_clicks):
    session.clear()
    return "/signin"