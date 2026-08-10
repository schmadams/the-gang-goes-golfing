# target path: frontend/src/pages/home.py (full replacement)
import time
from contextlib import contextmanager

import dash
import dash_bootstrap_components as dbc
import requests
from dash import Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL
from layouts.panel_navbar import build_panel_navbar

dash.register_page(__name__, path="/", name="Home")


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
    # Works for local search results and freshly-imported courses -- they
    # all share these same field names.
    label = course["club_name"]
    if course.get("course_name"):
        label += f" — {course['course_name']}"
    location = course.get("county") or course.get("postcode")
    return f"{label} ({location})" if location else label


def layout():
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        # Not signed in (or a stale/incomplete session) — bounce to sign-in
        session.clear()
        return dcc.Location(pathname="/signin", id="redirect-to-signin", refresh=True)

    with _timed(f"GET /group-players/player/{player_id}"):
        groups_resp = requests.get(f"{API_BASE_URL}/group-players/player/{player_id}")
    groups = (
        [row["groups"] for row in groups_resp.json()]
        if groups_resp.status_code == 200
        else []
    )

    if groups:
        groups_section = html.Div(
            className="t3g-groups-list",
            children=[
                # NOTE: /groups/<slug> doesn't exist yet — placeholder link
                # for the group home page we haven't built.
                dcc.Link(
                    group["name"],
                    href=f"/groups/{group['slug']}",
                    className="t3g-group-item",
                )
                for group in groups
            ],
        )
    else:
        groups_section = html.P(
            "You're not in any groups yet.", className="t3g-empty-state"
        )

    # Preload every cached club (a few hundred rows -- cheap) so the round
    # course picker can filter client-side, same approach as My Account's
    # home-course field. Value here is the internal course id (not just a
    # display string), since we need it to look up/import tees.
    with _timed("GET /courses/"):
        courses_resp = requests.get(f"{API_BASE_URL}/courses/")
    courses = courses_resp.json() if courses_resp.status_code == 200 else []
    course_options = [{"label": _course_label(c), "value": c["id"]} for c in courses]

    return html.Div(
        className="t3g-page",
        children=[
            html.Div(
                className="t3g-panel-grid",
                children=[
                    html.Div(
                        className="t3g-panel",
                        children=[
                            build_panel_navbar(
                                "Your Groups",
                                action=[
                                    html.Button(
                                        "Join Group",
                                        id="join-group-button",
                                        className="t3g-panel-action-button t3g-panel-action-button--secondary",
                                    ),
                                    html.Button(
                                        "Create Group",
                                        id="create-group-button",
                                        className="t3g-panel-action-button",
                                    ),
                                ],
                            ),
                            html.Div(groups_section, className="t3g-panel-body"),
                        ],
                    ),
                    html.Div(
                        className="t3g-panel",
                        children=[
                            build_panel_navbar(
                                "Rounds History",
                                action=html.Button(
                                    "Upload New Round",
                                    id="upload-round-button",
                                    className="t3g-panel-action-button",
                                ),
                            ),
                            html.Div(
                                html.P(
                                    "No rounds recorded yet.",
                                    className="t3g-empty-state",
                                ),
                                className="t3g-panel-body",
                            ),
                        ],
                    ),
                ],
            ),
            dbc.Modal(
                id="join-group-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Join a Group")),
                    dbc.ModalBody(
                        [
                            dbc.Input(
                                id="join-group-uuid-input",
                                placeholder="Group ID (UUID)",
                                type="text",
                            ),
                            html.Div(
                                id="join-group-error", className="text-danger mt-2"
                            ),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button(
                                "Cancel", id="join-group-cancel", color="secondary"
                            ),
                            dbc.Button("Join", id="join-group-submit", color="primary"),
                        ]
                    ),
                ],
            ),
            dcc.Location(id="join-group-redirect", refresh=True),
            dbc.Modal(
                id="create-group-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Create a Group")),
                    dbc.ModalBody(
                        [
                            dbc.Input(
                                id="create-group-name-input",
                                placeholder="Group name",
                                type="text",
                                className="mb-2",
                            ),
                            dbc.Textarea(
                                id="create-group-description-input",
                                placeholder="Description (optional)",
                            ),
                            html.Div(
                                id="create-group-error", className="text-danger mt-2"
                            ),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button(
                                "Cancel", id="create-group-cancel", color="secondary"
                            ),
                            dbc.Button(
                                "Create", id="create-group-submit", color="primary"
                            ),
                        ]
                    ),
                ],
            ),
            dcc.Location(id="create-group-redirect", refresh=True),
            dbc.Modal(
                id="upload-round-modal",
                is_open=False,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Upload New Round")),
                    dbc.ModalBody(
                        [
                            dcc.Dropdown(
                                id="upload-round-course",
                                placeholder="Search for the course you played",
                                options=course_options,
                                searchable=True,
                                clearable=True,
                                className="mb-2 t3g-course-dropdown",
                            ),
                            dcc.Loading(
                                type="circle",
                                children=dcc.Dropdown(
                                    id="upload-round-tee",
                                    placeholder="Select tees",
                                    options=[],
                                    disabled=True,
                                    className="mb-1 t3g-course-dropdown",
                                ),
                            ),
                            html.Div(id="upload-round-tee-status", className="t3g-empty-state mt-1"),
                            html.Div(id="upload-round-error", className="text-danger mt-2"),
                            html.Div(id="upload-round-status", className="mt-2"),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button(
                                "Cancel", id="upload-round-cancel", color="secondary"
                            ),
                            dbc.Button(
                                "Continue",
                                id="upload-round-continue",
                                color="primary",
                                disabled=True,
                            ),
                        ]
                    ),
                ],
            ),
        ],
    )


@callback(
    Output("join-group-modal", "is_open"),
    Output("join-group-error", "children"),
    Output("join-group-redirect", "pathname"),
    Input("join-group-button", "n_clicks"),
    Input("join-group-cancel", "n_clicks"),
    Input("join-group-submit", "n_clicks"),
    State("join-group-uuid-input", "value"),
    prevent_initial_call=True,
)
def handle_join_group(open_clicks, cancel_clicks, submit_clicks, group_uuid):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "join-group-button":
        return True, "", dash.no_update

    if triggered_id == "join-group-cancel":
        return False, "", dash.no_update

    if triggered_id == "join-group-submit":
        if not group_uuid:
            return True, "Enter a group ID.", dash.no_update

        player_id = session.get("player_id")
        with _timed("POST /group-players/"):
            response = requests.post(
                f"{API_BASE_URL}/group-players/",
                json={"group_id": group_uuid, "player_id": player_id},
            )

        if response.status_code == 201:
            return False, "", "/"

        if response.status_code == 422:
            return True, "That doesn't look like a valid group ID.", dash.no_update

        # NOTE: the backend doesn't yet distinguish "group not found" from
        # "already a member" — both currently surface as a generic error.
        return (
            True,
            "Couldn't join that group. Check the ID, or you may already be a member.",
            dash.no_update,
        )

    return dash.no_update, dash.no_update, dash.no_update


@callback(
    Output("create-group-modal", "is_open"),
    Output("create-group-error", "children"),
    Output("create-group-redirect", "pathname"),
    Input("create-group-button", "n_clicks"),
    Input("create-group-cancel", "n_clicks"),
    Input("create-group-submit", "n_clicks"),
    State("create-group-name-input", "value"),
    State("create-group-description-input", "value"),
    prevent_initial_call=True,
)
def handle_create_group(open_clicks, cancel_clicks, submit_clicks, name, description):
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "create-group-button":
        return True, "", dash.no_update

    if triggered_id == "create-group-cancel":
        return False, "", dash.no_update

    if triggered_id == "create-group-submit":
        if not name:
            return True, "Enter a group name.", dash.no_update

        player_id = session.get("player_id")
        with _timed("POST /groups/"):
            group_resp = requests.post(
                f"{API_BASE_URL}/groups/",
                json={
                    "name": name,
                    "description": description or None,
                    "admin_player_id": player_id,
                },
            )

        if group_resp.status_code == 409:
            return (
                True,
                "A group with a similar name already exists. Try a different name.",
                dash.no_update,
            )
        if group_resp.status_code != 201:
            return True, "Couldn't create the group. Try again.", dash.no_update

        new_group = group_resp.json()

        # Automatically add the creator as a member too, so the group shows
        # up in their own groups list right away. Best-effort: if this call
        # fails, the group still exists and can be joined manually by ID.
        with _timed("POST /group-players/ (auto-join creator)"):
            requests.post(
                f"{API_BASE_URL}/group-players/",
                json={"group_id": new_group["id"], "player_id": player_id},
            )

        return False, "", "/"

    return dash.no_update, dash.no_update, dash.no_update


@callback(
    Output("upload-round-modal", "is_open"),
    Output("upload-round-course", "value"),
    Input("upload-round-button", "n_clicks"),
    Input("upload-round-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_upload_round_modal(open_clicks, cancel_clicks):
    # Resetting the course value on open (via the Output below) also
    # triggers load_tees_for_course(None), which clears the tee dropdown --
    # so the modal always starts fresh rather than showing a stale
    # selection from last time it was opened.
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "upload-round-button":
        return True, None

    return False, dash.no_update


@callback(
    Output("upload-round-tee", "options"),
    Output("upload-round-tee", "disabled"),
    Output("upload-round-tee-status", "children"),
    Output("upload-round-error", "children"),
    Input("upload-round-course", "value"),
    prevent_initial_call=True,
)
def load_tees_for_course(course_id):
    if not course_id:
        return [], True, "", ""

    # Fetches the cached scorecard, importing it from the live API first if
    # this is the first time anyone's picked this course -- the only place
    # in the round-upload flow that can spend one of our monthly API
    # requests, and only ever once per course. This call can be much slower
    # than the others above on a cache miss (hits the external API + several
    # DB writes) -- watch this line specifically when diagnosing slowness.
    with _timed(f"POST /courses/{course_id}/scorecard"):
        response = requests.post(f"{API_BASE_URL}/courses/{course_id}/scorecard")

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", "Couldn't load tees for that course.")
        except ValueError:
            detail = "Couldn't load tees for that course."
        return [], True, "", detail

    course = response.json()
    tees = course.get("tees", [])

    if not tees:
        return [], True, "No tee data available for this course yet.", ""

    tee_options = [
        {
            "label": f"{tee['name']} tees" + (f" (Par {tee['par']})" if tee.get("par") else ""),
            "value": tee["id"],
        }
        for tee in tees
    ]

    return tee_options, False, "", ""


@callback(
    Output("upload-round-continue", "disabled"),
    Input("upload-round-course", "value"),
    Input("upload-round-tee", "value"),
    prevent_initial_call=True,
)
def toggle_continue_button(course_id, tee_id):
    return not (course_id and tee_id)


@callback(
    Output("upload-round-status", "children"),
    Input("upload-round-continue", "n_clicks"),
    State("upload-round-course", "value"),
    State("upload-round-tee", "value"),
    prevent_initial_call=True,
)
def handle_continue_round(n_clicks, course_id, tee_id):
    if not course_id or not tee_id:
        return html.Span("Select a course and tees first.", className="text-danger")

    # Score entry isn't built yet -- this just confirms the selection so
    # far. Next step: hook this up to an actual scorecard entry screen.
    return html.Span(
        "Course and tees selected — score entry comes next.",
        className="text-success",
    )