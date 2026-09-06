# target path: frontend/src/pages/my_account.py (full replacement)
import base64
import time
from contextlib import contextmanager

import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/my-account", name="My Account")


# Tournament-style pill subnav shared between this page and friends.py --
# real navigation (dcc.Link, not a client-side tab toggle), since these
# stay two separate registered pages/routes each with their own auth
# check and callbacks rather than being merged into one. Same "small
# duplicated pure render function per page" convention as the rest of
# the app (see analysis.py/profile.py's own docstrings) rather than
# cross-importing. The bottom nav's own "Account" tab still just links
# straight to /my-account -- this subnav is what actually lets you get
# to Friends from there (and back) without a fifth bottom-nav icon.
_ACCOUNT_TAB_BASE = "t3g-tournament-tab"
_ACCOUNT_TAB_ACTIVE = "t3g-tournament-tab t3g-tournament-tab--active"

# WHS caps Handicap Index at 54.0 -- same duplicated-as-a-plain-constant
# pattern club.py/tournament.py's own _MAX_HANDICAP_INDEX use for their
# min/max handicap steppers (the frontend talks to the backend over HTTP
# only, so there's no shared Python module to import this from). -10.0 is
# the same practical floor those two use, reused here since a self-
# entered Handicap Index can go negative too (elite/plus players).
_MAX_HANDICAP_INDEX = 54.0
_MIN_HANDICAP_FLOOR = -10.0


def _manual_handicap_stepper():
    """+/- stepper for manually adding a handicap entry -- same
    .t3g-stepper/-button/-value/-row/-col markup (and the
    t3g-stepper--horizontal modifier) live_round.py's Shots/Putts entry
    and club.py/tournament.py's own min/max handicap steppers all already
    use, just moving in 0.1 steps instead of whole numbers, since a
    Handicap Index is always expressed to one decimal place.
    adjust_manual_handicap_stepper below holds the actual float value in
    account-handicap-value-store; the display div just mirrors it as
    text."""
    return html.Div(
        className="t3g-stepper t3g-stepper--horizontal",
        children=[
            html.Button("–", id="account-handicap-minus", className="t3g-stepper-button", n_clicks=0),
            html.Div("–", id="account-handicap-display", className="t3g-stepper-value"),
            html.Button("+", id="account-handicap-plus", className="t3g-stepper-button", n_clicks=0),
            dcc.Store(id="account-handicap-value-store", data=None),
        ],
    )


def _account_subnav(active):
    return html.Div(
        className="t3g-tournament-subnav",
        children=html.Div(
            className="t3g-tournament-subnav-inner",
            children=html.Div(
                className="t3g-tournament-tabs",
                children=[
                    dcc.Link(
                        "My Account",
                        href="/my-account",
                        className=_ACCOUNT_TAB_ACTIVE if active == "account" else _ACCOUNT_TAB_BASE,
                    ),
                    dcc.Link(
                        "My Profile",
                        href="/my-account/profile",
                        className=_ACCOUNT_TAB_ACTIVE if active == "profile" else _ACCOUNT_TAB_BASE,
                    ),
                    dcc.Link(
                        "Friends",
                        href="/friends",
                        className=_ACCOUNT_TAB_ACTIVE if active == "friends" else _ACCOUNT_TAB_BASE,
                    ),
                    dcc.Link(
                        "Calendar",
                        href="/calendar",
                        className=_ACCOUNT_TAB_ACTIVE if active == "calendar" else _ACCOUNT_TAB_BASE,
                    ),
                ],
            ),
        ),
    )


def _refresh_href():
    # dcc.Location only actually reloads the browser when the value it's
    # given differs from what's already loaded -- outputting the same
    # "/my-account" pathname while already sitting on /my-account is a
    # no-op, so Save/Add Handicap Entry looked like they did nothing.
    # Appending a cache-busting query string guarantees the value always
    # changes, which is what actually makes the refresh fire -- same fix
    # as friends.py's _refresh_href().
    return f"/my-account?_r={time.time()}"


@contextmanager
def _timed(label: str):
    """
    Logs how long a call to our own API took, tagged "own API" so it's
    obvious from the console which layer (frontend->backend, vs the
    backend's own external/database calls, logged separately in
    backend/services/courses.py) any slowness is actually coming from.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[TIMING] own API      {elapsed_ms:8.1f}ms  {label}")


def _course_label(course):
    # Works for local search results, external search candidates, and
    # freshly-imported courses -- they all share these same field names.
    label = course["club_name"]
    if course.get("course_name"):
        label += f" — {course['course_name']}"
    location = course.get("county") or course.get("postcode")
    return f"{label} ({location})" if location else label


def layout(**kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="my-account-redirect", refresh=True)

    # The bottom nav's "Account" tab (layouts/bottom_nav.py) links here,
    # not to /friends directly -- Friends only shows up one tap deeper,
    # via this page's own subnav. But the badge on that tab is keyed to
    # the "friends" category, and from wherever's looking at that tab,
    # landing on My Account at all IS "I've checked Account" -- nobody
    # should have to specifically click through to the Friends subnav
    # tab before the number on the tab they already tapped goes away.
    # See friends.py's own version of this same call for the other half
    # (someone who lands straight on /friends via a notification link,
    # bypassing My Account entirely).
    try:
        requests.post(f"{API_BASE_URL}/notifications/{player_id}/read/friends")
    except requests.RequestException:
        pass

    with _timed(f"GET /players/{player_id}"):
        player_resp = requests.get(f"{API_BASE_URL}/players/{player_id}")
    player = player_resp.json() if player_resp.status_code == 200 else {}

    with _timed(f"GET /handicaps/player/{player_id}"):
        handicaps_resp = requests.get(f"{API_BASE_URL}/handicaps/player/{player_id}")
    handicaps = handicaps_resp.json() if handicaps_resp.status_code == 200 else []

    # Both current handicaps side by side, plus which one this player has
    # set as their default -- see get_player_handicap_sources's own
    # docstring in backend/services/handicaps.py. This is what actually
    # lets someone tell "my T3G handicap" and "my manually entered one"
    # apart, rather than just seeing whichever's most recently written.
    with _timed(f"GET /handicaps/player/{player_id}/sources"):
        handicap_sources_resp = requests.get(f"{API_BASE_URL}/handicaps/player/{player_id}/sources")
    handicap_sources = handicap_sources_resp.json() if handicap_sources_resp.status_code == 200 else {}
    t3g_handicap = (handicap_sources.get("t3g") or {}).get("handicap")
    manual_handicap = (handicap_sources.get("manual") or {}).get("handicap")
    preferred_source = handicap_sources.get("preferred_source") or "t3g"

    # Preload every cached club once on page load (a few hundred rows --
    # cheap) so the dropdown can filter client-side as the player types,
    # rather than round-tripping to the backend on every keystroke.
    with _timed("GET /courses/"):
        courses_resp = requests.get(f"{API_BASE_URL}/courses/")
    courses = courses_resp.json() if courses_resp.status_code == 200 else []
    course_options = [{"label": _course_label(c), "value": _course_label(c)} for c in courses]

    home_course = player.get("home_course")
    if home_course and not any(opt["value"] == home_course for opt in course_options):
        # Whatever's already saved should always be selectable, even if
        # it's somehow missing from the cached list.
        course_options.append({"label": home_course, "value": home_course})

    photo_url = player.get("profile_picture_url")

    if handicaps:
        handicap_table = dbc.Table(
            children=[
                html.Thead(html.Tr([html.Th("Handicap"), html.Th("Source"), html.Th("Valid From")])),
                html.Tbody(
                    [
                        html.Tr(
                            [
                                html.Td(h["handicap"]),
                                html.Td(
                                    "T3G" if h.get("source", "t3g") == "t3g" else "Manual",
                                    className=(
                                        "t3g-handicap-source-badge t3g-handicap-source-badge--t3g"
                                        if h.get("source", "t3g") == "t3g"
                                        else "t3g-handicap-source-badge t3g-handicap-source-badge--manual"
                                    ),
                                ),
                                html.Td(h["valid_from"]),
                            ]
                        )
                        for h in handicaps
                    ]
                ),
            ],
            bordered=False,
            hover=True,
            responsive=True,
            className="t3g-handicap-table",
        )
    else:
        handicap_table = html.P(
            "No handicap history yet.", className="t3g-empty-state"
        )

    def _source_summary_row(label, value, source_key):
        # One of the two headline numbers above the full history --
        # "Not set yet" rather than blank when that source has no entry
        # at all (very likely for Manual, on a player who's never used
        # it), so the row still explains itself instead of just showing
        # nothing next to a label.
        display_value = f"{value:.1f}" if value is not None else "Not set yet"
        is_preferred = preferred_source == source_key
        return html.Div(
            className="t3g-handicap-source-summary-row",
            children=[
                html.Span(label, className="t3g-handicap-source-summary-label"),
                html.Span(display_value, className="t3g-handicap-source-summary-value"),
                html.Span("Preferred", className="t3g-handicap-source-preferred-badge")
                if is_preferred
                else None,
            ],
        )

    handicap_source_summary = html.Div(
        className="t3g-handicap-source-summary",
        children=[
            _source_summary_row("T3G Handicap", t3g_handicap, "t3g"),
            _source_summary_row("Manual Handicap", manual_handicap, "manual"),
        ],
    )

    handicap_source_toggle = html.Div(
        className="t3g-handicap-source-toggle",
        children=[
            html.Div("Use for rounds & tournaments by default", className="t3g-stepper-label mb-1"),
            html.Div(
                className="t3g-scorecard-view-toggle",
                children=[
                    html.Button(
                        "T3G Handicap",
                        id="account-handicap-source-t3g",
                        className=(
                            "t3g-scorecard-view-toggle-button t3g-scorecard-view-toggle-button--active"
                            if preferred_source == "t3g"
                            else "t3g-scorecard-view-toggle-button"
                        ),
                        n_clicks=0,
                    ),
                    html.Button(
                        "Manual Handicap",
                        id="account-handicap-source-manual",
                        className=(
                            "t3g-scorecard-view-toggle-button t3g-scorecard-view-toggle-button--active"
                            if preferred_source == "manual"
                            else "t3g-scorecard-view-toggle-button"
                        ),
                        n_clicks=0,
                    ),
                ],
            ),
            html.Div(id="account-handicap-source-message", className="mt-2"),
            dcc.Store(id="account-handicap-source-store", data=preferred_source),
            dcc.Location(id="account-handicap-source-redirect", refresh=True),
        ],
    )

    return html.Div(
        className="t3g-page",
        children=[
            _account_subnav("account"),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("Your Player ID"),
                    html.Div(
                        className="t3g-panel-body t3g-player-id-panel-body",
                        children=[
                            html.P(
                                "Share this with friends so they can send you a friend request.",
                                className="t3g-empty-state mb-2",
                            ),
                            html.Div(
                                className="t3g-player-id-row",
                                children=[
                                    html.Code(
                                        player_id,
                                        id="account-player-id-value",
                                        className="t3g-player-id-value",
                                    ),
                                    html.Button(
                                        "Copy",
                                        id="account-player-id-copy",
                                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                                        n_clicks=0,
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("Profile Picture"),
                    html.Div(
                        className="t3g-panel-body t3g-photo-panel-body",
                        children=[
                            html.Img(
                                id="account-photo-preview",
                                src=photo_url or "",
                                className="t3g-profile-photo",
                                style={} if photo_url else {"display": "none"},
                            ),
                            dcc.Upload(
                                id="account-photo-upload",
                                children=html.Button(
                                    "Upload Photo", className="t3g-panel-action-button"
                                ),
                                accept="image/*",
                                style={"display": "inline-block"},
                            ),
                            html.Div(
                                id="account-photo-error", className="text-danger mt-2"
                            ),
                            dcc.Location(id="account-photo-redirect", refresh=True),
                        ],
                    ),
                ],
            ),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("My Details"),
                    html.Div(
                        className="t3g-panel-body t3g-account-form",
                        children=[
                            dbc.Input(
                                id="account-first-name",
                                placeholder="First name",
                                value=player.get("first_name"),
                                className="mb-2",
                            ),
                            dbc.Input(
                                id="account-surname",
                                placeholder="Surname",
                                value=player.get("surname"),
                                className="mb-2",
                            ),
                            dbc.Input(
                                id="account-nickname",
                                placeholder="Nickname",
                                value=player.get("nickname"),
                                className="mb-2",
                            ),
                            dcc.DatePickerSingle(
                                id="account-dob",
                                date=player.get("date_of_birth"),
                                placeholder="Date of birth",
                                className="mb-2",
                            ),
                            dcc.Dropdown(
                                id="account-home-course",
                                placeholder="Start typing your home course...",
                                options=course_options,
                                value=home_course,
                                searchable=True,
                                clearable=True,
                                # All cached clubs are preloaded above, so
                                # filtering happens client-side as you type
                                # -- no backend round-trip per keystroke.
                                className="mb-1 mt-2 t3g-course-dropdown",
                            ),
                            html.Button(
                                "Can't find it? Search all UK clubs",
                                id="account-course-external-toggle",
                                className="t3g-link-button mb-2",
                                n_clicks=0,
                            ),
                            dbc.Modal(
                                id="account-course-external-modal",
                                is_open=False,
                                children=[
                                    dbc.ModalHeader("Search UK Golf Clubs"),
                                    dbc.ModalBody(
                                        children=[
                                            dbc.Input(
                                                id="account-course-external-query",
                                                placeholder="Club name, e.g. Wentworth",
                                                className="mb-2",
                                            ),
                                            html.Button(
                                                "Search",
                                                id="account-course-external-search",
                                                className="t3g-panel-action-button",
                                            ),
                                            html.Div(
                                                id="account-course-external-results",
                                                className="mt-3",
                                            ),
                                            dcc.Store(id="account-course-candidates-store"),
                                        ],
                                    ),
                                ],
                            ),
                            dbc.Input(
                                id="account-eg-number",
                                placeholder="England Golf handicap number",
                                value=player.get("england_golf_number"),
                                className="mb-2",
                            ),
                            dbc.Input(
                                id="account-phone",
                                placeholder="Phone number",
                                value=player.get("phone_number"),
                                className="mb-2",
                            ),
                            html.Button(
                                "Save",
                                id="account-save-button",
                                className="t3g-panel-action-button mt-2",
                            ),
                            html.Div(id="account-save-message", className="mt-2"),
                            dcc.Location(id="account-save-redirect", refresh=True),
                        ],
                    ),
                ],
            ),
            html.Div(
                className="t3g-panel",
                children=[
                    build_panel_navbar("Handicap"),
                    html.Div(
                        className="t3g-panel-body",
                        children=[
                            handicap_source_summary,
                            handicap_source_toggle,
                            html.Hr(),
                            handicap_table,
                            html.Hr(),
                            html.Div("New handicap", className="t3g-stepper-label mb-1"),
                            _manual_handicap_stepper(),
                            html.Button(
                                "Add Handicap Entry",
                                id="account-handicap-submit",
                                className="t3g-panel-action-button mt-2",
                            ),
                            html.Div(id="account-handicap-message", className="mt-2"),
                            dcc.Location(id="account-handicap-redirect", refresh=True),
                        ],
                    ),
                ],
            ),
        ],
    )


dash.clientside_callback(
    """
    function(n_clicks, player_id) {
        if (!n_clicks) {
            return window.dash_clientside.no_update;
        }
        navigator.clipboard.writeText(player_id);
        return "Copied!";
    }
    """,
    Output("account-player-id-copy", "children"),
    Input("account-player-id-copy", "n_clicks"),
    State("account-player-id-value", "children"),
    prevent_initial_call=True,
)


@callback(
    Output("account-course-external-modal", "is_open"),
    Input("account-course-external-toggle", "n_clicks"),
    State("account-course-external-modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_external_search_modal(n_clicks, is_open):
    return not is_open


@callback(
    Output("account-course-external-results", "children"),
    Output("account-course-candidates-store", "data"),
    Input("account-course-external-search", "n_clicks"),
    State("account-course-external-query", "value"),
    prevent_initial_call=True,
)
def run_external_course_search(n_clicks, query):
    # Only fires when the player explicitly presses "Search" -- this is the
    # one action in the app that spends one of our monthly UK Golf API
    # requests, so it should never happen automatically.
    if not query:
        return html.Span("Enter a club name to search.", className="text-danger"), dash.no_update

    with _timed("GET /courses/external-search"):
        response = requests.get(f"{API_BASE_URL}/courses/external-search", params={"query": query})

    if response.status_code != 200:
        # Surface the backend's actual error (which now includes RapidAPI's
        # real rejection reason) instead of a generic message, so we don't
        # have to guess from PyCharm's console every time.
        try:
            detail = response.json().get("detail", "Search failed. Try again.")
        except ValueError:
            detail = "Search failed. Try again."
        return html.Span(detail, className="text-danger"), dash.no_update

    candidates = response.json()

    if not candidates:
        return html.Span("No clubs found. Try a different spelling.", className="t3g-empty-state"), []

    results = html.Div(
        [
            html.Button(
                _course_label(candidate),
                id={"type": "account-course-candidate", "index": i},
                className="t3g-course-candidate-button",
                n_clicks=0,
            )
            for i, candidate in enumerate(candidates)
        ]
    )
    return results, candidates


@callback(
    Output("account-home-course", "value"),
    Output("account-home-course", "options", allow_duplicate=True),
    Output("account-course-external-modal", "is_open", allow_duplicate=True),
    Input({"type": "account-course-candidate", "index": ALL}, "n_clicks"),
    State("account-course-candidates-store", "data"),
    prevent_initial_call=True,
)
def select_external_course(n_clicks_list, candidates):
    # Fires whenever the set of result buttons changes too (all n_clicks=0),
    # so bail out unless one was actually clicked.
    if not candidates or not any(n_clicks_list):
        raise PreventUpdate

    triggered_id = dash.ctx.triggered_id
    if triggered_id is None:
        raise PreventUpdate

    candidate = candidates[triggered_id["index"]]

    # Caches the full scorecard for this course (if not already cached).
    with _timed("POST /courses/import"):
        response = requests.post(f"{API_BASE_URL}/courses/import", json=candidate)

    if response.status_code != 201:
        raise PreventUpdate

    course = response.json()
    label = _course_label(course)

    return label, [{"label": label, "value": label}], False


@callback(
    Output("account-photo-error", "children"),
    Output("account-photo-redirect", "href"),
    Input("account-photo-upload", "contents"),
    State("account-photo-upload", "filename"),
    prevent_initial_call=True,
)
def handle_photo_upload(contents, filename):
    if not contents:
        return "", dash.no_update

    player_id = session.get("player_id")

    header, encoded = contents.split(",", 1)
    file_bytes = base64.b64decode(encoded)
    content_type = header.split(";")[0].replace("data:", "") or "image/jpeg"

    with _timed(f"POST /players/{player_id}/profile-picture"):
        response = requests.post(
            f"{API_BASE_URL}/players/{player_id}/profile-picture",
            files={"file": (filename or "photo.jpg", file_bytes, content_type)},
        )

    if response.status_code != 200:
        # Surface the backend's real error detail when there is one
        # (upload_profile_picture_route returns a specific message via
        # ImageUploadError for a Supabase Storage failure, e.g. a missing
        # bucket) rather than always showing the same generic message --
        # same "read response.json().get('detail', ...)" pattern already
        # used for club invites below.
        try:
            detail = response.json().get("detail", "Couldn't upload that photo. Try again.")
            if not isinstance(detail, str):
                detail = "Couldn't upload that photo. Try again."
        except ValueError:
            detail = "Couldn't upload that photo. Try again."
        return detail, dash.no_update

    # Reload the page so the new photo, freshly fetched, shows up. Cache-bust
    # via _refresh_href() rather than the plain "/my-account" path -- if
    # you're already sitting on /my-account (the normal case here),
    # dcc.Location only reloads when the value actually changes.
    return "", _refresh_href()


@callback(
    Output("account-save-message", "children"),
    Output("account-save-redirect", "href"),
    Input("account-save-button", "n_clicks"),
    State("account-first-name", "value"),
    State("account-surname", "value"),
    State("account-nickname", "value"),
    State("account-dob", "date"),
    State("account-home-course", "value"),
    State("account-eg-number", "value"),
    State("account-phone", "value"),
    prevent_initial_call=True,
)
def handle_save_profile(
    n_clicks, first_name, surname, nickname, dob, home_course, eg_number, phone
):
    player_id = session.get("player_id")

    payload = {
        "first_name": first_name,
        "surname": surname,
        "nickname": nickname,
        "date_of_birth": dob,
        "home_course": home_course,
        "england_golf_number": eg_number,
        "phone_number": phone,
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}

    with _timed(f"PATCH /players/{player_id}"):
        response = requests.patch(f"{API_BASE_URL}/players/{player_id}", json=payload)

    if response.status_code == 200:
        return "", _refresh_href()

    return (
        html.Span("Couldn't save changes. Try again.", className="text-danger"),
        dash.no_update,
    )


@callback(
    Output("account-handicap-value-store", "data"),
    Output("account-handicap-display", "children"),
    Input("account-handicap-plus", "n_clicks"),
    Input("account-handicap-minus", "n_clicks"),
    State("account-handicap-value-store", "data"),
    prevent_initial_call=True,
)
def adjust_manual_handicap_stepper(plus_clicks, minus_clicks, current):
    # Same asymmetric "plus starts an unset field at 0, minus starts it
    # at -1" logic club.py's _adjust_handicap_stepper already uses for
    # the min/max handicap steppers (see that function's own docstring
    # for why minus isn't just plus's mirror) -- just 0.1 at a time
    # instead of whole numbers, and rounded after every step since binary
    # floats can't represent 0.1 exactly (0.1 + 0.1 + 0.1 != 0.3) and that
    # drift would otherwise show up in the display after enough clicks.
    triggered_id = dash.ctx.triggered_id
    value = current

    if triggered_id == "account-handicap-plus":
        value = 0.0 if value is None else round(min(value + 0.1, _MAX_HANDICAP_INDEX), 1)
    elif triggered_id == "account-handicap-minus":
        value = -0.1 if value is None else (round(value - 0.1, 1) if value > _MIN_HANDICAP_FLOOR else None)

    return value, f"{value:.1f}" if value is not None else "–"


@callback(
    Output("account-handicap-message", "children"),
    Output("account-handicap-redirect", "href"),
    Input("account-handicap-submit", "n_clicks"),
    State("account-handicap-value-store", "data"),
    prevent_initial_call=True,
)
def handle_add_handicap(n_clicks, handicap_value):
    if handicap_value is None:
        return (
            html.Span("Set a handicap value first.", className="text-danger"),
            dash.no_update,
        )

    player_id = session.get("player_id")
    with _timed("POST /handicaps/"):
        response = requests.post(
            f"{API_BASE_URL}/handicaps/",
            json={"player_id": player_id, "handicap": handicap_value},
        )

    if response.status_code == 201:
        return "", _refresh_href()

    return (
        html.Span("Couldn't update handicap. Try again.", className="text-danger"),
        dash.no_update,
    )

@callback(
    Output("account-handicap-source-message", "children"),
    Output("account-handicap-source-redirect", "href"),
    Input("account-handicap-source-t3g", "n_clicks"),
    Input("account-handicap-source-manual", "n_clicks"),
    prevent_initial_call=True,
)
def handle_set_preferred_handicap_source(t3g_clicks, manual_clicks):
    # Which button was actually pressed -- mirrors adjust_manual_handicap_stepper's
    # use of dash.ctx.triggered_id above rather than trusting the two n_clicks
    # values directly, since both Inputs fire this callback and only one of them
    # corresponds to the real click.
    triggered_id = dash.ctx.triggered_id
    if triggered_id == "account-handicap-source-t3g":
        source = "t3g"
    elif triggered_id == "account-handicap-source-manual":
        source = "manual"
    else:
        raise PreventUpdate

    player_id = session.get("player_id")
    with _timed(f"PATCH /players/{player_id}"):
        response = requests.patch(
            f"{API_BASE_URL}/players/{player_id}",
            json={"preferred_handicap_source": source},
        )

    if response.status_code == 200:
        # Reload so the toggle's --active state and both summary rows'
        # "Preferred" badge re-render against the new preference, same
        # pattern as every other save callback on this page.
        return "", _refresh_href()

    return (
        html.Span("Couldn't update your preference. Try again.", className="text-danger"),
        dash.no_update,
    )