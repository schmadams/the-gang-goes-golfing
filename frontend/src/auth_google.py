# target path: frontend/src/auth_google.py
"""Google "Sign in with Google" -- the two Flask routes this needs
(/auth/google/login and /auth/google/callback) can't be Dash pages --
Dash Pages' layout() returns a component tree to render, not an HTTP
redirect, and this flow is nothing but redirects (out to Google's own
consent screen, then back here, then on to "/"). So these are registered
directly on the Flask app object that dash.Dash wraps (app.server, see
app.py's register_google_auth_routes(server) call) -- the same object
that already owns the browser session (flask.session) signin.py's own
_log_in sets on a manually-entered-email sign-in. Reusing that same
helper here is what makes a Google sign-in "count" as a login the rest
of the app already understands, rather than needing every page to learn
a second way a user might be logged in.

Setup required before this works:
  - A Google Cloud Console OAuth 2.0 Client ID (Web application type)
  - GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET set in the frontend's
    environment
  - That client's "Authorized redirect URIs" must include this app's own
    <origin>/auth/google/callback for every origin it's actually served
    from (e.g. http://127.0.0.1:8050/auth/google/callback for local dev,
    plus whatever the real deployed frontend origin is) -- Google
    rejects the whole flow with a redirect_uri_mismatch error otherwise.
"""
import os
import secrets
from urllib.parse import urlencode

import requests
from flask import redirect, request, session
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from config import API_BASE_URL

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def _redirect_uri() -> str:
    # Built from the incoming request rather than hardcoded, so this works
    # unchanged across local dev / staging / prod as long as each origin's
    # own callback URL is separately allow-listed in Google Cloud Console
    # (see this module's docstring) -- request.url_root already reflects
    # whatever host the request actually arrived on.
    return request.url_root.rstrip("/") + "/auth/google/callback"


def google_login():
    if not GOOGLE_CLIENT_ID:
        return redirect("/signin?google_error=not_configured")

    # CSRF protection: a random, unguessable value we can check came back
    # unchanged on the callback -- without this, an attacker could craft a
    # link straight to our own /auth/google/callback with a code obtained
    # from their own Google account, tricking a signed-in victim's browser
    # into linking or creating an account under the attacker's identity.
    state = secrets.token_urlsafe(24)
    session["google_oauth_state"] = state

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        # Forces the account chooser instead of silently reusing whichever
        # Google account happens to already be signed in on this browser --
        # friends sharing a laptop/trip device is a realistic case here.
        "prompt": "select_account",
    }
    return redirect(f"{_AUTHORIZATION_ENDPOINT}?{urlencode(params)}")


def google_callback():
    error = request.args.get("error")
    if error:
        # e.g. "access_denied" -- the person hit Cancel on Google's consent
        # screen. Not a real failure, just send them back to try again.
        return redirect("/signin")

    returned_state = request.args.get("state")
    expected_state = session.pop("google_oauth_state", None)
    if not returned_state or not expected_state or returned_state != expected_state:
        return redirect("/signin?google_error=state_mismatch")

    code = request.args.get("code")
    if not code:
        return redirect("/signin?google_error=missing_code")

    token_response = requests.post(
        _TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
        },
    )
    if token_response.status_code != 200:
        return redirect("/signin?google_error=token_exchange_failed")

    id_token_jwt = token_response.json().get("id_token")
    if not id_token_jwt:
        return redirect("/signin?google_error=token_exchange_failed")

    try:
        # This is the step that actually makes trusting anything below
        # safe -- verifies id_token_jwt's signature against Google's own
        # published public keys (fetched and cached internally by this
        # call) and checks it was issued for OUR client_id, rather than
        # just base64-decoding the JWT's payload and hoping it's genuine.
        claims = google_id_token.verify_oauth2_token(
            id_token_jwt, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError:
        return redirect("/signin?google_error=invalid_token")

    if not claims.get("email_verified"):
        # Google itself isn't sure this email belongs to this person --
        # e.g. a Google Workspace account signing in with a work address
        # its admin never verified. Refuse rather than let an unverified
        # email silently auto-link onto an existing player_accounts row
        # (see player_accounts.py's link_google_id docstring for why that
        # linking step specifically relies on this check happening first).
        return redirect("/signin?google_error=email_not_verified")

    account_response = requests.post(
        f"{API_BASE_URL}/player-accounts/google",
        json={
            "email": claims["email"],
            "google_id": claims["sub"],
            "first_name": claims.get("given_name") or "",
            "surname": claims.get("family_name") or "",
        },
    )
    if account_response.status_code != 200:
        return redirect("/signin?google_error=account_failed")

    # Local import, not a top-of-file one -- app.py imports this module
    # (to call register_google_auth_routes) before it instantiates
    # dash.Dash(use_pages=True), and Dash Pages auto-discovery is what's
    # supposed to be the thing that first imports pages/signin.py (which
    # calls dash.register_page() at its own module level) -- that
    # discovery only happens safely *during* that dash.Dash(...) call.
    # Importing pages.signin up at this module's top level ran that
    # import (and so signin.py's register_page() call) too early, before
    # app instantiation, which Dash rejects outright with a PageError.
    # Deferring it to here means it only ever runs at actual request time,
    # long after app.py has finished setting everything up -- by then
    # pages.signin is already safely imported and cached, so this is just
    # a normal (free) module lookup, not a re-run of register_page().
    from pages.signin import _log_in

    _log_in(account_response.json())
    return redirect("/")


def register_google_auth_routes(server) -> None:
    server.add_url_rule("/auth/google/login", "google_login", google_login)
    server.add_url_rule("/auth/google/callback", "google_callback", google_callback)