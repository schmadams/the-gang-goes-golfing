# target path: frontend/src/app.py (full replacement)
import logging
import os

import dash
import dash_bootstrap_components as dbc
from dash import dcc, html
from flask import session

from components.spinner import golf_swing_spinner
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

# Applies a saved light/dark preference (see layouts/navbar.py's theme
# toggle button and assets/theme.css's two token blocks) before Dash's
# own JS bundle even starts, by setting the data-theme attribute
# straight from localStorage in a small inline script that runs as the
# very first thing in <head>. Doing this in serve_layout() or a Dash
# callback instead would only set the attribute after the page had
# already painted once with whichever theme the CSS defaults to, which
# shows up as a visible flash between the wrong theme and the right one
# on every hard load. Defaults to dark for a first-time visitor with no
# saved preference yet, matching this app's own default.
app.index_string = """<!DOCTYPE html>
<html>
    <head>
        <script>
            (function () {
                var theme = "dark";
                try {
                    // Reads the exact same localStorage slot the
                    // theme-store dcc.Store (storage_type="local", see
                    // serve_layout below) persists to, dash-core-
                    // components writes a dcc.Store's value to
                    // localStorage under a key equal to the component's
                    // own id, JSON-encoded, so this has to stay in sync
                    // with that id string and encoding rather than
                    // inventing a separate key of its own.
                    var raw = window.localStorage.getItem("theme-store");
                    var saved = raw ? JSON.parse(raw) : null;
                    if (saved === "light" || saved === "dark") {
                        theme = saved;
                    }
                } catch (err) {
                    // localStorage can throw in some privacy modes, or raw
                    // could be malformed JSON, dark stays as the default.
                }
                document.documentElement.setAttribute("data-theme", theme);
            })();
        </script>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

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
    children = [
        # storage_type="local" persists the choice across tabs and
        # browser restarts, not just this one session, the same way a
        # user would expect a light/dark preference to stick. Present on
        # every page (not just gated behind login like the navbar below)
        # so the toggle also works from signin.py. The initial value
        # here is only what a fresh browser with no saved preference
        # yet sees; app.py's index_string script above is what actually
        # avoids a flash of the wrong theme on load, this Store exists
        # so navbar.py's toggle callback has something to read and write.
        dcc.Store(id="theme-store", storage_type="local", data="dark"),
    ]

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

    # dash.page_container's actual content depends on whichever page's
    # layout() function runs -- most of them make blocking requests.get()
    # calls to the backend before returning anything -- so there's a real
    # gap between "the JS bundle finished mounting" (which is as far as
    # assets/loading.css's ._dash-loading boot placeholder covers -- see
    # that file) and "this page's content actually arrived". Wrapping
    # page_container itself in dcc.Loading covers that second gap with the
    # same golf swing animation, so the spinner holds continuously through
    # both phases of a hard page load instead of disappearing the instant
    # React mounts and leaving a blank gap before content pops in. This
    # also means every client-side navigation (dcc.Link clicks) gets the
    # same spinner while its target page's layout() is still running,
    # which is a reasonable bonus rather than a regression -- previously
    # navigating showed nothing at all during that wait.
    #
    # delay_hide=2000 -- once this spinner does show, keep it up for at
    # least 2 seconds even if the page actually finished loading sooner.
    # Without this, a fast page (or a client-side dcc.Link nav that
    # resolves almost instantly) made the golf swing animation flash on
    # and cut off mid-swing, which read as glitchy rather than smooth --
    # dcc.Loading's default behavior shows/hides strictly based on actual
    # loading state with no minimum, so any load under 2s would flash
    # instead of playing even one full swing. delay_show is left at its
    # default (0) on purpose -- the ask was only "don't let it disappear
    # too fast once it's up", not "wait before showing it", so a genuinely
    # slow load still shows the spinner immediately rather than adding
    # more dead time up front.
    #
    # target_components={"_pages_content": "children"} -- without this,
    # dcc.Loading shows for ANY pending callback anywhere in the app whose
    # Output lives inside page_container, which in practice is nearly
    # every callback that exists (a score save on the live round page, a
    # dropdown search, a hole-by-hole nav click, etc.), since they're all
    # rendered somewhere under page_container. That's what made this
    # spinner feel "over-reactive" -- it was covering the whole page for
    # routine in-page interactions, not just real navigations. "_pages_
    # content" is Dash Pages' own internal id (dash.dash._ID_CONTENT) for
    # the div whose children get swapped on an actual page load/
    # navigation -- scoping target_components to just that one id+prop
    # means this spinner now only reacts to genuine page loads, and every
    # other callback on the page updates normally with no overlay at all.
    #
    # id="page-loading-spinner" -- pairs with a CSS override in assets/
    # spinner.css. dcc.Loading's own DOM has the spinner sit in a div
    # that's position:absolute + height:100% inside this wrapper --
    # "100%" of the wrapper's own height, which is set by whichever
    # page's real content is mounted underneath (still there the whole
    # time, just visibility:hidden while loading, which still occupies
    # its full layout height). Every page is a different height, and the
    # wrapper resizes the instant the destination page's content actually
    # mounts (even before the overlay lifts) -- so the spinner's own
    # vertical centering point was drifting between navigations, which is
    # what actually read as "the loading sign changes height a bit".
    # Pinning that spinner div to a fixed height via CSS (see spinner.css)
    # keeps it centered in the exact same spot on every navigation,
    # completely independent of the destination page's real height.
    children.append(
        dcc.Loading(
            dash.page_container,
            id="page-loading-spinner",
            custom_spinner=golf_swing_spinner(height="7.5rem"),
            parent_style={"minHeight": "60vh"},
            delay_hide=2000,
            target_components={"_pages_content": "children"},
        )
    )

    if session.get("logged_in"):
        # Rendered once per hard load, same as build_navbar() above --
        # which item is "active" is handled reactively inside
        # build_bottom_nav()'s own highlight_active_bottom_nav_tab
        # callback (also keyed off _pages_location), not here. Placed last
        # in the DOM since it's position:fixed in CSS (assets/bottom_nav.
        # css) and only actually shows up below the phone-width
        # breakpoint -- desktop never sees it.
        children.append(build_bottom_nav())

    return html.Div(children, className="t3g-app-shell")


app.layout = serve_layout

if __name__ == "__main__":
    # Production (Railway, etc.) runs this app via gunicorn targeting
    # app:server directly (see Dockerfile.frontend) -- gunicorn never
    # executes this __main__ block at all, so debug mode is never reachable
    # that way. This guard is just a safety net for the unlikely case
    # someone runs `python app.py` directly on a real server instead:
    # debug=True's dev server isn't built for real traffic, and its
    # in-browser debugger can execute arbitrary code if it's ever reachable
    # from outside, which is a real risk on a public host. Local dev is
    # unaffected -- DASH_DEBUG is unset there, so this still defaults to
    # the same debug=True hot-reloading behavior as before.
    debug_mode = os.environ.get("DASH_DEBUG", "true").lower() == "true"
    app.run(debug=debug_mode)  # app.run_server is deprecated