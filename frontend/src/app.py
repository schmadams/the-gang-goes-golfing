# target path: frontend/src/app.py (full replacement)
import logging
import os

import dash
import dash_bootstrap_components as dbc
from dash import html
from flask import session

from layouts.navbar import build_navbar
from layouts.subnav import build_subnav

# Quiet the default Flask dev-server request logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = dash.Dash(
    __name__,
    use_pages=True,                     # auto-discovers files in pages/
    suppress_callback_exceptions=True,  # needed since page-specific callbacks
                                         # aren't in the layout until their page loads
    external_stylesheets=[dbc.themes.BOOTSTRAP],
)

server = app.server  # exposed so gunicorn/wsgi can target `app:server` in prod
server.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")  # required for Flask sessions


def serve_layout():
    # A function (not a static value) so this re-evaluates on every fresh
    # page load and picks up the current session — otherwise the navbar
    # wouldn't know whether you're logged in.
    children = []

    if session.get("logged_in"):
        children.append(build_navbar())
        # The subnav itself (not just whether it shows at all) is decided
        # reactively in layouts/subnav.py's render_subnav callback, keyed
        # off Dash Pages' own pathname tracker (_pages_location) -- this
        # function only runs once per hard page load, but navigating
        # between pages via dcc.Link is client-side and never re-runs it,
        # so a pathname check here alone couldn't hide the subnav again
        # once you'd left "/".
        children.append(html.Div(id="subnav-container"))

    children.append(dash.page_container)

    return html.Div(
        children,
        style={"background": "#261C67", "minHeight": "100vh", "width": "100%"},
    )


app.layout = serve_layout

if __name__ == "__main__":
    app.run(debug=True)  # auto-reloads on file changes; app.run_server is deprecated