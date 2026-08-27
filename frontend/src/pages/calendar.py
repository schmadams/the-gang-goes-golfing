# target path: frontend/src/pages/calendar.py (full replacement)
"""
Personal calendar -- a real month grid (not a list), mixing three kinds
of events onto whatever day they fall on: historic completed rounds,
published tournament tee times, and tournament entries still awaiting a
tee time. All three come from one backend call (GET /players/{id}/
calendar -- see get_calendar_events' own docstring in backend/services/
calendar.py for exactly how they're merged and de-duplicated); this file
only turns that flat list into a grid.

Reached via the My Account subnav (see _account_subnav in this file and
its duplicate in friends.py) rather than a top-nav link or a sixth
bottom-nav slot -- same reasoning as Friends/My Profile already living
there instead of getting their own permanent nav entry.

Month navigation is the same "?query param -> layout(**kwargs)" deep-
linking pattern tournament.py's own ?tab= already uses -- Prev/Next are
plain dcc.Link's to /calendar?year=Y&month=M, not a callback, so the URL
itself is always the source of truth for which month is showing (you can
bookmark or share a link to a specific month) and there's no client-side
state to keep in sync with it.

Each day cell shows a small color-coded dot per event (not the full
text chip this page used to render) -- clicking a day opens a modal with
that day's full details, the same click-a-row-to-open-a-modal shape
tournament.py's leaderboard already uses for
toggle_tournament_leaderboard_scorecard (pattern-matched Input on every
day cell's n_clicks, a dcc.Store carrying the data the modal body needs,
a phantom-trigger guard since the grid rebuilds -- fresh n_clicks=0 --
every time the month changes).
"""
import calendar as calendar_module
from datetime import date

import dash
import dash_bootstrap_components as dbc
import requests
from dash import ALL, Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate
from flask import session

from config import API_BASE_URL

dash.register_page(__name__, path="/calendar", name="Calendar")

_ACCOUNT_TAB_BASE = "t3g-tournament-tab"
_ACCOUNT_TAB_ACTIVE = "t3g-tournament-tab t3g-tournament-tab--active"

# One label + CSS modifier per event type (see get_calendar_events'
# CATEGORIES-style comment in backend/services/calendar.py) -- the
# frontend is the only place that needs to know these three map to
# specific colors, the backend just hands over the bare type string.
# Reused as the color source for both the day-cell dots and the modal's
# event rows, and for the page legend at the top.
_EVENT_TYPE_LABELS = {
    "round": "t3g-calendar-chip--round",
    "scheduled": "t3g-calendar-chip--scheduled",
    "tournament": "t3g-calendar-chip--tournament",
}


def _account_subnav(active):
    """Duplicated from my_account.py/friends.py on purpose -- same "small
    per-page duplicated helper" convention as every other page-local
    subnav copy in this app (see friends.py's own docstring on this
    exact function for the reasoning)."""
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


def _event_dot(event):
    """One small color-coded dot per event on a day cell -- replaces the
    old full-text chip. title= gives a native browser tooltip on hover
    (desktop) as a small bonus, but the real detail view is the click-
    to-open modal below, since dots obviously can't show a title/subtitle
    themselves."""
    modifier = _EVENT_TYPE_LABELS.get(event.get("type"), "")
    return html.Span(
        className=f"t3g-calendar-dot {modifier}".strip(),
        title=event.get("title"),
    )


def _day_cell(day, is_current_month, is_today, events_for_day):
    classes = ["t3g-calendar-day"]
    if not is_current_month:
        classes.append("t3g-calendar-day--outside")
    if is_today:
        classes.append("t3g-calendar-day--today")

    return html.Div(
        id={"type": "calendar-day-cell", "date": day.isoformat()},
        n_clicks=0,
        className=" ".join(classes),
        children=[
            html.Div(str(day.day), className="t3g-calendar-day-number"),
            html.Div(
                [_event_dot(e) for e in events_for_day],
                className="t3g-calendar-day-dots",
            ),
        ],
    )


def _month_grid(year, month, events_by_date):
    # firstweekday=6 -- weeks start Sunday, matching every US-style
    # calendar (and this app's date formatting elsewhere, e.g. "D Mon
    # YYYY" rather than ISO order). monthdatescalendar pads the first/
    # last week with real date objects from the adjacent months rather
    # than None/blank cells -- simpler to render (every cell is always a
    # real day with a real number) at the cost of showing a handful of
    # dimmed days that belong to neighboring months, which is exactly how
    # Google/Apple Calendar's own month view looks too.
    cal = calendar_module.Calendar(firstweekday=6)
    weeks = cal.monthdatescalendar(year, month)
    today = date.today()

    weekday_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    header_row = html.Div(
        [html.Div(label, className="t3g-calendar-weekday") for label in weekday_labels],
        className="t3g-calendar-weekday-row",
    )

    week_rows = []
    for week in weeks:
        cells = [
            _day_cell(
                day,
                is_current_month=(day.month == month),
                is_today=(day == today),
                events_for_day=events_by_date.get(day.isoformat(), []),
            )
            for day in week
        ]
        week_rows.append(html.Div(cells, className="t3g-calendar-week-row"))

    return html.Div([header_row, html.Div(week_rows, className="t3g-calendar-weeks")], className="t3g-calendar-grid")


def _prev_next_month(year, month):
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return (prev_year, prev_month), (next_year, next_month)


def _day_detail_event_row(event):
    modifier = _EVENT_TYPE_LABELS.get(event.get("type"), "")
    content = [
        html.Span(className=f"t3g-calendar-dot t3g-calendar-modal-event-dot {modifier}".strip()),
        html.Div(
            [
                html.Span(event.get("title") or "Event", className="t3g-calendar-modal-event-title"),
                html.Span(event.get("subtitle"), className="t3g-calendar-modal-event-subtitle") if event.get("subtitle") else None,
            ],
            className="t3g-calendar-modal-event-text",
        ),
    ]
    row_class = "t3g-calendar-modal-event"
    if event.get("url"):
        return dcc.Link(content, href=event["url"], className=row_class)
    return html.Div(content, className=row_class)


def _day_detail_body(events_for_day):
    if not events_for_day:
        return html.Div("No events on this day.", className="t3g-calendar-modal-empty")
    return html.Div([_day_detail_event_row(e) for e in events_for_day])


def _day_detail_modal():
    """Opened by clicking any day cell in the grid (see
    toggle_calendar_day_modal below) -- shows every event on that day.
    centered=True + the .t3g-calendar-day-modal CSS override (see
    calendar.css) together satisfy this feature's specific "open from the
    middle, don't drop from the top" requirement: centered=True sets the
    dialog's resting position to the vertical middle of the screen,
    the CSS override changes Bootstrap's default open transition from a
    downward slide into a scale-from-center grow, so it doesn't visibly
    travel from above on its way there either. Scoped to this modal only
    (via className, not a global override) since no other modal in the
    app was asked to change how it opens."""
    return dbc.Modal(
        id="calendar-day-modal",
        is_open=False,
        centered=True,
        className="t3g-calendar-day-modal",
        children=[
            dbc.ModalHeader(dbc.ModalTitle(id="calendar-day-modal-title")),
            dbc.ModalBody(id="calendar-day-modal-body"),
            dbc.ModalFooter(dbc.Button("Close", id="calendar-day-modal-close", color="secondary")),
        ],
    )


def layout(year=None, month=None, **kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="calendar-redirect-signin", refresh=True)

    today = date.today()
    try:
        year = int(year) if year else today.year
        month = int(month) if month else today.month
    except (TypeError, ValueError):
        # A malformed/tampered query string (?year=abc) falls back to the
        # current month rather than raising -- same "don't let a bad
        # query param break the page" treatment as tournament.py's own
        # ?tab= handling.
        year, month = today.year, today.month
    month = min(max(month, 1), 12)

    response = requests.get(f"{API_BASE_URL}/players/{player_id}/calendar")
    events = response.json() if response.status_code == 200 else []

    events_by_date = {}
    for event in events:
        events_by_date.setdefault(event["date"], []).append(event)

    (prev_year, prev_month), (next_year, next_month) = _prev_next_month(year, month)
    month_label = date(year, month, 1).strftime("%B %Y")

    return html.Div(
        className="t3g-page",
        children=[
            _account_subnav("calendar"),
            html.Div(
                className="t3g-calendar-header",
                children=[
                    dcc.Link("‹", href=f"/calendar?year={prev_year}&month={prev_month}", className="t3g-calendar-nav-arrow"),
                    html.H2(month_label, className="t3g-calendar-month-label"),
                    dcc.Link("›", href=f"/calendar?year={next_year}&month={next_month}", className="t3g-calendar-nav-arrow"),
                ],
            ),
            html.Div(
                className="t3g-calendar-legend",
                children=[
                    html.Span([html.Span(className="t3g-calendar-legend-dot t3g-calendar-chip--round"), "Played"], className="t3g-calendar-legend-item"),
                    html.Span([html.Span(className="t3g-calendar-legend-dot t3g-calendar-chip--scheduled"), "Tee time set"], className="t3g-calendar-legend-item"),
                    html.Span([html.Span(className="t3g-calendar-legend-dot t3g-calendar-chip--tournament"), "Entered"], className="t3g-calendar-legend-item"),
                ],
            ),
            _month_grid(year, month, events_by_date),
            dcc.Store(id="calendar-events-store", data=events_by_date),
            _day_detail_modal(),
        ],
    )


@callback(
    Output("calendar-day-modal", "is_open"),
    Output("calendar-day-modal-title", "children"),
    Output("calendar-day-modal-body", "children"),
    Input({"type": "calendar-day-cell", "date": ALL}, "n_clicks"),
    Input("calendar-day-modal-close", "n_clicks"),
    State("calendar-events-store", "data"),
    prevent_initial_call=True,
)
def toggle_calendar_day_modal(day_clicks, close_clicks, events_by_date):
    # Every day cell shares this one callback (date is ALL, there's no
    # per-cell callback) -- same phantom-trigger guard as tournament.py's
    # toggle_tournament_leaderboard_scorecard, since the whole grid gets
    # rebuilt (fresh n_clicks=0 cells) on every month navigation.
    triggered_id = dash.ctx.triggered_id

    if triggered_id == "calendar-day-modal-close":
        return False, dash.no_update, dash.no_update

    if not triggered_id or not any(day_clicks or []):
        raise PreventUpdate

    event_date = triggered_id["date"]
    events_by_date = events_by_date or {}
    events_for_day = events_by_date.get(event_date, [])

    title = date.fromisoformat(event_date).strftime("%A, %d %B %Y")
    body = _day_detail_body(events_for_day)
    return True, title, body