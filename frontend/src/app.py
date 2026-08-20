# target path: frontend/src/app.py (full replacement)
import logging
import os

import dash
import dash_bootstrap_components as dbc
from dash import html
from flask import session

from layouts.bottom_nav import build_bottom_nav
from layouts.navbar import build_navbar
from layouts.subnav import build_subnav

# Quiet the default Flask dev-server request logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = dash.Dash(
    __name__,
    use_pages=True,                     # auto-discovers files in pages/
    suppress_callback_exceptions=True,  # needed since page-specific callbacks
                                         # aren't in the layout until their page loads
    external_stylesheets=[
        dbc.themes.BOOTSTRAP,
        # Icon font for the mobile bottom nav (layouts/bottom_nav.py) --
        # dash.html has no native SVG element support (no html.Svg/Path/
        # etc.), and the bottom nav's icons need to live inside real,
        # clickable dcc.Link components rather than a static
        # dangerously_allow_html blob, so an icon font is the simplest fit
        # here rather than inlining per-icon SVG markup by hand.
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css",
    ],
)

server = app.server  # exposed so gunicorn/wsgi can target `app:server` in prod
server.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")  # required for Flask sessions

# Dash Pages matches a URL against every registered path_template in
# page_registry order and returns the FIRST match -- it does not prefer the
# most specific one. Its matching also isn't slash-aware: <var> compiles to
# a greedy (.*), so a short template like /clubs/<slug> also matches a
# longer path like /clubs/<slug>/tournaments/<tournament_id>. Since pages
# are registered in file-import order (roughly alphabetical -- club.py
# before tournament.py), every visit to a tournament page was matching
# club.py's route first, with the whole "tournaments/<id>" tail folded into
# slug, which then 404'd against the clubs API and rendered "Club not
# found." Re-sorting the registry so templates with more path segments
# (i.e. more specific/nested routes) are tried first fixes this for any
# current or future nested route under /clubs/<slug>/... -- shorter,
# less-specific templates still match correctly once nothing deeper does.
dash.page_registry = dict(
    sorted(
        dash.page_registry.items(),
        # Pages registered with a static `path` (e.g. home.py's "/") instead
        # of a `path_template` have path_template=None -- they have no <var>
        # segments to be greedy about, so they're never the ambiguous side
        # of a collision and can safely sort last.
        key=lambda item: (item[1]["path_template"] or "").count("/"),
        reverse=True,
    )
)
dash._pages.PAGE_REGISTRY.clear()
dash._pages.PAGE_REGISTRY.update(dash.page_registry)


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

    if session.get("logged_in"):
        # Rendered once per hard load, same as build_navbar() above --
        # which item is "active" is handled reactively inside
        # build_bottom_nav()'s own highlight_active_bottom_nav_tab
        # callback (also keyed off _pages_location), not here. Placed last
        # in the DOM since it's position:fixed in CSS (assets/bottom_nav.
        # css) and only actually shows up below the phone-width
        # breakpoint -- desktop never sees it.
        children.append(build_bottom_nav())

    return html.Div(
        children,
        style={"background": "#261C67", "minHeight": "100vh", "width": "100%"},
    )


app.layout = serve_layout

if __name__ == "__main__":
    app.run(debug=True)  # auto-reloads on file changes; app.run_server is deprecated