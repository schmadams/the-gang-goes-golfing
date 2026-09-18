# target path: frontend/src/pages/signin.py (full replacement)
import dash
import dash_bootstrap_components as dbc
import requests
from dash import Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL

dash.register_page(__name__, path="/signin", name="Sign In")

# NOTE: email-only, no password, for the manual "Continue" path below. This
# is intentionally a placeholder for anyone who hasn't linked Google yet —
# anyone who knows a registered email can sign in as that player through
# it. Google sign-in (added alongside this) doesn't have that problem,
# since it's Google that verifies who's actually signing in, not us — see
# auth_google.py. Fine for a private trip app among friends during dev,
# not fine once this is exposed more broadly if the email-only path is
# still the only option by then.
#
# NOTE: registering always creates a brand-new `players` row. If someone
# was already added to `players` ahead of time (e.g. by a trip organizer)
# and then registers here, they'll end up with a duplicate player record
# rather than being linked to the existing one. Fine for now, worth
# revisiting if that becomes a real problem.

# This page was the one place in the app still rendering raw, unthemed
# dbc.Card/dbc.Input/dbc.Button markup -- every other page went through
# the app-wide dark retheme (see assets/theme.css's own history) via
# custom .t3g-* classes, but signin.py never got touched since it predates
# that pass and nothing about it visually broke in a way that surfaced
# until Google sign-in made this page worth actually looking at again.
# assets/signin.css (new file, alongside this one) now themes it the same
# way -- var(--t3g-surface)/--t3g-border/--t3g-accent card and inputs,
# flat Archivo Narrow CTA button matching .t3g-panel-action-button
# elsewhere -- instead of Bootstrap's stock white-card-blue-button look.
#
# Layout shape (email primary, small square provider icon(s) below a "or
# continue with" divider) follows the same structure real sites like
# Booking.com use for this exact screen. Google's own recommended button
# copy/branding is a whole separate asset kit -- this reuses the site's
# own icon-button treatment (Font Awesome's "google" brand glyph, already
# loaded app-wide for the bottom nav/navbar, see app.py's
# external_stylesheets) rather than pulling in Google's button JS/CSS
# bundle for what's otherwise just a styled link to /auth/google/login.
# Only one icon shows since Google's the only provider this app supports
# right now -- the row is built to hold more later without changing shape.
_GOOGLE_ERROR_MESSAGES = {
    "not_configured": "Google sign-in isn't set up yet.",
    "state_mismatch": "That sign-in attempt expired. Please try again.",
    "missing_code": "Google sign-in was cancelled or didn't complete.",
    "token_exchange_failed": "Couldn't verify that with Google. Please try again.",
    "invalid_token": "Couldn't verify that with Google. Please try again.",
    "email_not_verified": "That Google account's email isn't verified. Try a different sign-in method.",
    "account_failed": "Something went wrong finishing sign-in. Please try again.",
}


def layout(**kwargs):
    # **kwargs (not a bare empty signature) -- Dash Pages passes any query
    # string on the URL through to layout() as keyword arguments. Every
    # other page's layout() already tolerates this; this one didn't, which
    # is exactly what broke Sign out: navbar.py's signout-redirect used to
    # leave a stale "?_r=..." cache-buster attached to the "/signin"
    # redirect (fixed separately in navbar.py), and landing here with that
    # still on the URL raised "layout() got an unexpected keyword argument
    # '_r'" since this signature accepted none at all. Kept regardless of
    # that other fix, since anyone could still land on /signin?whatever
    # some other way (a bookmark, a stale link, retyping the URL by hand).
    #
    # google_error -- auth_google.py's callback route redirects back here
    # with ?google_error=<code> on any failure (state mismatch, a token
    # Google itself wouldn't vouch for, etc.) rather than raising inside
    # that route, since there's no Dash callback context there to show an
    # error through -- a plain query-param + a one-time render-time lookup
    # here is the whole mechanism.
    google_error = _GOOGLE_ERROR_MESSAGES.get(kwargs.get("google_error"))

    return html.Div(
        html.Div(
            [
                dcc.Location(id="signin-redirect", refresh=True),
                html.H3("Sign in", className="t3g-signin-title"),
                html.P(
                    "Enter your email to sign in, or create an account.",
                    className="t3g-signin-subtitle",
                ),
                dbc.Input(id="email", placeholder="Email", type="email", className="mb-3"),
                dbc.Button(
                    "Continue",
                    id="continue-button",
                    className="t3g-signin-primary-button",
                ),
                html.Div(id="signin-error", className="t3g-signin-error"),
                html.Div(
                    [
                        html.Span("or use one of these options"),
                    ],
                    className="t3g-signin-divider",
                ),
                html.Div(
                    [
                        html.A(
                            html.I(className="fa-brands fa-google"),
                            href="/auth/google/login",
                            className="t3g-signin-social-button",
                            title="Continue with Google",
                            **{"aria-label": "Continue with Google"},
                        ),
                    ],
                    className="t3g-signin-social-row",
                ),
                html.Div(google_error, className="t3g-signin-error") if google_error else None,
                html.Div(
                    id="register-section",
                    style={"display": "none"},
                    children=[
                        html.Hr(),
                        html.H5("No account found for that email"),
                        html.P("Create one below to get started.", className="t3g-signin-subtitle"),
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
                            className="t3g-signin-primary-button",
                        ),
                        html.Div(id="register-error", className="t3g-signin-error"),
                    ],
                ),
            ],
            className="t3g-signin-card",
        ),
        className="t3g-signin-page",
    )


def _log_in(account: dict) -> None:
    session["logged_in"] = True
    session["player_id"] = account["player_id"]
    session["name"] = account["name"]
    session["email"] = account["email"]


@callback(
    Output("signin-redirect", "href"),
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