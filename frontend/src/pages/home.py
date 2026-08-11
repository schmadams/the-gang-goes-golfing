# target path: frontend/src/pages/home.py (full replacement)
import time
from contextlib import contextmanager

import dash
import dash_bootstrap_components as dbc
import requests
from dash import Input, Output, State, callback, dcc, html
from flask import session

from components.scorecard import format_handicap, history_score_mark_class, live_badge, round_header_label
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


def _round_scorecard_card(round_data, player_initial, player_label):
    """Renders one completed round as a mini traditional scorecard: hole
    numbers across the top, a par row, and a scores row with the same
    birdie/bogey marks used on the live round page, plus OUT/IN/TOT/HCP/NET
    summary columns."""
    holes_by_number = {h["hole_number"]: h for h in (round_data.get("holes") or [])}
    front9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(1, 10)]
    back9 = [holes_by_number.get(n, {"hole_number": n}) for n in range(10, 19)]

    def _sum_par(hole_subset):
        pars = [h.get("par") for h in hole_subset if h.get("par") is not None]
        return sum(pars) if pars else None

    def _sum_strokes(hole_subset):
        strokes = [h.get("strokes") for h in hole_subset if h.get("strokes") is not None]
        return sum(strokes) if strokes else None

    out_par, in_par = _sum_par(front9), _sum_par(back9)
    tot_par = out_par + in_par if out_par is not None and in_par is not None else None
    out_strokes, in_strokes = _sum_strokes(front9), _sum_strokes(back9)
    total_strokes = round_data.get("total_strokes")

    handicap = round_data.get("handicap")
    hcp_display = format_handicap(handicap)
    net_display = round(total_strokes - handicap) if (handicap is not None and total_strokes is not None) else "—"

    def _hole_number_cells(hole_subset):
        return [html.Th(str(h["hole_number"])) for h in hole_subset]

    def _par_cells(hole_subset):
        return [html.Td(h.get("par") if h.get("par") is not None else "—") for h in hole_subset]

    def _score_cells(hole_subset):
        return [
            html.Td(
                html.Span(
                    h.get("strokes") if h.get("strokes") is not None else "—",
                    className=history_score_mark_class(h.get("strokes"), h.get("par")),
                )
            )
            for h in hole_subset
        ]

    header_row = html.Tr(
        [html.Th("Hole", className="t3g-history-row-label")]
        + _hole_number_cells(front9)
        + [html.Th("OUT")]
        + _hole_number_cells(back9)
        + [html.Th("IN"), html.Th("TOT"), html.Th("HCP"), html.Th("NET")]
    )

    par_row = html.Tr(
        className="t3g-history-par-row",
        children=(
            [html.Td("Par", className="t3g-history-row-label")]
            + _par_cells(front9)
            + [html.Td(out_par if out_par is not None else "—", className="t3g-history-summary-cell")]
            + _par_cells(back9)
            + [
                html.Td(in_par if in_par is not None else "—", className="t3g-history-summary-cell"),
                html.Td(tot_par if tot_par is not None else "—", className="t3g-history-summary-cell"),
                html.Td(""),
                html.Td(""),
            ]
        ),
    )

    player_row = html.Tr(
        className="t3g-history-player-row",
        children=(
            [
                html.Td(
                    html.Div(
                        [
                            html.Div(player_initial, className="t3g-history-player-avatar"),
                            html.Span(player_label),
                        ],
                        className="t3g-history-player-cell",
                    )
                )
            ]
            + _score_cells(front9)
            + [html.Td(out_strokes if out_strokes is not None else "—", className="t3g-history-summary-cell")]
            + _score_cells(back9)
            + [
                html.Td(in_strokes if in_strokes is not None else "—", className="t3g-history-summary-cell"),
                html.Td(total_strokes if total_strokes is not None else "—", className="t3g-history-summary-cell"),
                html.Td(hcp_display, className="t3g-history-summary-cell"),
                html.Td(net_display, className="t3g-history-summary-cell"),
            ]
        ),
    )

    is_live = round_data.get("status") == "in_progress"
    header_children = [html.Span(round_header_label(round_data), className="t3g-round-card-title")]
    if is_live:
        header_children.append(
            html.Div(live_badge(), className="t3g-round-card-header-actions")
        )

    return html.Div(
        className="t3g-round-card",
        children=[
            html.Div(
                header_children,
                className="t3g-round-card-header",
            ),
            html.Div(
                className="t3g-history-scorecard-wrap",
                children=html.Table(
                    className="t3g-history-scorecard-table",
                    children=[
                        html.Thead([header_row, par_row]),
                        html.Tbody([player_row]),
                    ],
                ),
            ),
        ],
    )


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

    with _timed(f"GET /rounds/player/{player_id}"):
        rounds_resp = requests.get(f"{API_BASE_URL}/rounds/player/{player_id}")
    rounds_history = rounds_resp.json() if rounds_resp.status_code == 200 else []

    if rounds_history:
        # Only needed for the scorecard's avatar/name -- skip the call
        # entirely when there's no history to render.
        with _timed(f"GET /players/{player_id}"):
            player_resp = requests.get(f"{API_BASE_URL}/players/{player_id}")
        player = player_resp.json() if player_resp.status_code == 200 else {}
        player_label = player.get("nickname") or player.get("first_name") or "You"
        player_initial = player_label[0].upper() if player_label else "Y"

        rounds_section = html.Div(
            className="t3g-rounds-list",
            children=[
                _round_scorecard_card(r, player_initial, player_label) for r in rounds_history
            ],
        )
    else:
        rounds_section = html.P(
            "No rounds recorded yet.", className="t3g-empty-state"
        )

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
                            html.Div(rounds_section, className="t3g-panel-body"),
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
                            html.Button(
                                "Can't find your course? Enter it manually",
                                id="upload-round-manual-toggle",
                                className="t3g-link-button mb-2",
                                n_clicks=0,
                            ),
                            html.Div(
                                id="upload-round-manual-fields",
                                style={"display": "none"},
                                children=[
                                    dbc.Input(
                                        id="upload-round-manual-club",
                                        placeholder="Club name",
                                        className="mb-2",
                                    ),
                                    dbc.Input(
                                        id="upload-round-manual-tee",
                                        placeholder="Tee name (e.g. White)",
                                        className="mb-2",
                                    ),
                                    html.P(
                                        "You'll enter par, length, and stroke index for each "
                                        "hole once the round starts.",
                                        className="t3g-empty-state",
                                    ),
                                ],
                            ),
                            dcc.Store(id="upload-round-manual-mode", data=False),
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
            dcc.Location(id="upload-round-redirect", refresh=True),
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
    Output("upload-round-redirect", "pathname", allow_duplicate=True),
    Output("upload-round-manual-mode", "data", allow_duplicate=True),
    Output("upload-round-manual-fields", "style", allow_duplicate=True),
    Output("upload-round-course", "style", allow_duplicate=True),
    Output("upload-round-tee-status", "style", allow_duplicate=True),
    Input("upload-round-button", "n_clicks"),
    Input("upload-round-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_upload_round_modal(open_clicks, cancel_clicks):
    # Resetting the course value on open (via the Output below) also
    # triggers load_tees_for_course(None), which clears the tee dropdown --
    # so the modal always starts fresh (manual mode off too) rather than
    # showing a stale selection from last time it was opened.
    triggered_id = dash.ctx.triggered_id
    reset_manual = (False, {"display": "none"}, {}, {})

    if triggered_id == "upload-round-button":
        player_id = session.get("player_id")
        with _timed(f"GET /rounds/active/{player_id}"):
            response = requests.get(f"{API_BASE_URL}/rounds/active/{player_id}")

        if response.status_code == 200:
            # Already have a live round in progress -- go straight there
            # instead of opening the modal. The backend would reject a
            # second one anyway (one-active-round-per-player), but this
            # avoids making them fill out the form just to be told no.
            return (False, dash.no_update, "/live-round", *reset_manual)

        return (True, None, dash.no_update, *reset_manual)

    return (False, dash.no_update, dash.no_update, *reset_manual)


@callback(
    Output("upload-round-manual-fields", "style", allow_duplicate=True),
    Output("upload-round-course", "style", allow_duplicate=True),
    Output("upload-round-tee-status", "style", allow_duplicate=True),
    Output("upload-round-manual-mode", "data", allow_duplicate=True),
    Input("upload-round-manual-toggle", "n_clicks"),
    State("upload-round-manual-mode", "data"),
    prevent_initial_call=True,
)
def toggle_manual_entry(n_clicks, is_manual):
    is_manual = not bool(is_manual)

    if is_manual:
        return {"display": "block"}, {"display": "none"}, {"display": "none"}, True

    return {"display": "none"}, {}, {}, False


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
    Input("upload-round-manual-mode", "data"),
    Input("upload-round-manual-club", "value"),
    Input("upload-round-manual-tee", "value"),
    prevent_initial_call=True,
)
def toggle_continue_button(course_id, tee_id, is_manual, manual_club, manual_tee):
    if is_manual:
        return not (manual_club and manual_tee)
    return not (course_id and tee_id)


@callback(
    Output("upload-round-status", "children"),
    Output("upload-round-redirect", "pathname", allow_duplicate=True),
    Input("upload-round-continue", "n_clicks"),
    State("upload-round-course", "value"),
    State("upload-round-tee", "value"),
    State("upload-round-manual-mode", "data"),
    State("upload-round-manual-club", "value"),
    State("upload-round-manual-tee", "value"),
    prevent_initial_call=True,
)
def handle_continue_round(n_clicks, course_id, tee_id, is_manual, manual_club, manual_tee):
    player_id = session.get("player_id")
    payload = {"player_id": player_id, "is_manual": bool(is_manual)}

    if is_manual:
        if not manual_club or not manual_tee:
            return (
                html.Span("Enter a club name and tee name first.", className="text-danger"),
                dash.no_update,
            )
        payload["manual_club_name"] = manual_club
        payload["manual_tee_name"] = manual_tee
    else:
        if not course_id or not tee_id:
            return (
                html.Span("Select a course and tees first.", className="text-danger"),
                dash.no_update,
            )
        payload["course_id"] = course_id
        payload["tee_id"] = tee_id

    with _timed("POST /rounds/"):
        response = requests.post(f"{API_BASE_URL}/rounds/", json=payload)

    if response.status_code == 201:
        return "", "/live-round"

    try:
        detail = response.json().get("detail", "Couldn't start the round.")
    except ValueError:
        detail = "Couldn't start the round."
    return html.Span(detail, className="text-danger"), dash.no_update