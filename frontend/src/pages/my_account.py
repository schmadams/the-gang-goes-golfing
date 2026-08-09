# target path: frontend/src/pages/my_account.py (full replacement)
import base64

import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/my-account", name="My Account")


def _course_label(course):
    # Works for local search results, external search candidates, and
    # freshly-imported courses -- they all share these same field names.
    label = course["club_name"]
    if course.get("course_name"):
        label += f" — {course['course_name']}"
    location = course.get("county") or course.get("postcode")
    return f"{label} ({location})" if location else label


def layout():
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="my-account-redirect", refresh=True)

    player_resp = requests.get(f"{API_BASE_URL}/players/{player_id}")
    player = player_resp.json() if player_resp.status_code == 200 else {}

    handicaps_resp = requests.get(f"{API_BASE_URL}/handicaps/player/{player_id}")
    handicaps = handicaps_resp.json() if handicaps_resp.status_code == 200 else []

    # The home-course dropdown starts with just whatever's already saved
    # (as a single option so Dash can display it) -- it doesn't preload
    # every course. Options are then filled in live as the player types,
    # searching our own cached `courses` table.
    home_course = player.get("home_course")
    initial_course_options = [{"label": home_course, "value": home_course}] if home_course else []

    photo_url = player.get("profile_picture_url")

    if handicaps:
        handicap_table = dbc.Table(
            children=[
                html.Thead(html.Tr([html.Th("Handicap"), html.Th("Valid From")])),
                html.Tbody(
                    [
                        html.Tr(
                            [html.Td(h["handicap"]), html.Td(h["valid_from"])]
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

    return html.Div(
        className="t3g-page",
        children=[
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
                                options=initial_course_options,
                                value=home_course,
                                searchable=True,
                                clearable=True,
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
                            handicap_table,
                            html.Hr(),
                            dbc.Input(
                                id="account-handicap-input",
                                placeholder="New handicap (e.g. 14.2)",
                                type="number",
                                step="0.1",
                                className="mb-2",
                            ),
                            html.Button(
                                "Add Handicap Entry",
                                id="account-handicap-submit",
                                className="t3g-panel-action-button",
                            ),
                            html.Div(id="account-handicap-message", className="mt-2"),
                            dcc.Location(id="account-handicap-redirect", refresh=True),
                        ],
                    ),
                ],
            ),
        ],
    )


@callback(
    Output("account-home-course", "options"),
    Input("account-home-course", "search_value"),
    prevent_initial_call=True,
)
def search_local_home_course(search_value):
    # Narrows the dropdown as the player types, searching only the courses
    # we've already cached -- never calls the external API.
    if not search_value or len(search_value) < 2:
        raise PreventUpdate

    response = requests.get(f"{API_BASE_URL}/courses/", params={"search": search_value})

    if response.status_code != 200:
        raise PreventUpdate

    courses = response.json()
    return [{"label": _course_label(c), "value": _course_label(c)} for c in courses]


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

    response = requests.get(f"{API_BASE_URL}/courses/external-search", params={"query": query})

    if response.status_code != 200:
        return html.Span("Search failed. Try again.", className="text-danger"), dash.no_update

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
    response = requests.post(f"{API_BASE_URL}/courses/import", json=candidate)

    if response.status_code != 201:
        raise PreventUpdate

    course = response.json()
    label = _course_label(course)

    return label, [{"label": label, "value": label}], False


@callback(
    Output("account-photo-error", "children"),
    Output("account-photo-redirect", "pathname"),
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

    response = requests.post(
        f"{API_BASE_URL}/players/{player_id}/profile-picture",
        files={"file": (filename or "photo.jpg", file_bytes, content_type)},
    )

    if response.status_code != 200:
        return "Couldn't upload that photo. Try again.", dash.no_update

    # Reload the page so the new photo, freshly fetched, shows up.
    return "", "/my-account"


@callback(
    Output("account-save-message", "children"),
    Output("account-save-redirect", "pathname"),
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

    response = requests.patch(f"{API_BASE_URL}/players/{player_id}", json=payload)

    if response.status_code == 200:
        return "", "/my-account"

    return (
        html.Span("Couldn't save changes. Try again.", className="text-danger"),
        dash.no_update,
    )


@callback(
    Output("account-handicap-message", "children"),
    Output("account-handicap-redirect", "pathname"),
    Input("account-handicap-submit", "n_clicks"),
    State("account-handicap-input", "value"),
    prevent_initial_call=True,
)
def handle_add_handicap(n_clicks, handicap_value):
    if not handicap_value:
        return (
            html.Span("Enter a handicap value.", className="text-danger"),
            dash.no_update,
        )

    player_id = session.get("player_id")
    response = requests.post(
        f"{API_BASE_URL}/handicaps/",
        json={"player_id": player_id, "handicap": float(handicap_value)},
    )

    if response.status_code == 201:
        return "", "/my-account"

    return (
        html.Span("Couldn't update handicap. Try again.", className="text-danger"),
        dash.no_update,
    )