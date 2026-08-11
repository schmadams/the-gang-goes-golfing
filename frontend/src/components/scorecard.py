# target path: frontend/src/components/scorecard.py (new file)
"""
Shared building blocks for anything that renders a round as a mini
traditional scorecard -- currently the home page's Rounds History panel
(compact) and the Scoring History page (detailed). Kept here instead of
duplicated in both pages so the birdie/bogey mark logic and round header
formatting only exist once.
"""
from dash import html


def history_score_mark_class(strokes, par):
    """Traditional scorecard marks against the compact .t3g-history-score
    cell: birdie -> circle, eagle (or better) -> double circle, bogey ->
    square, double bogey (or worse) -> double square. Same diff-from-par
    logic as live_round.py's _score_marking_class, just against a
    different base class since this renders inline in a dense table cell
    instead of a 44px button."""
    base = "t3g-history-score"
    if strokes is None or par is None:
        return base
    diff = strokes - par
    if diff <= -2:
        return f"{base} t3g-history-score-eagle"
    if diff == -1:
        return f"{base} t3g-history-score-birdie"
    if diff == 1:
        return f"{base} t3g-history-score-bogey"
    if diff >= 2:
        return f"{base} t3g-history-score-double-bogey"
    return base


def round_header_label(round_data):
    """One combined line -- club, course, tees, and date together, like
    the label on a real scorecard's cover. A live round has no
    completed_at yet, so it just omits the date rather than showing a
    blank."""
    label = round_data.get("club_name") or "Manually entered round"
    if round_data.get("course_name"):
        label += f" — {round_data['course_name']}"
    if round_data.get("tee_name"):
        label += f" ({round_data['tee_name']} tees)"

    date = (round_data.get("completed_at") or "")[:10]
    return f"{label} · {date}" if date else label


def live_badge():
    """Reuses the same pulsing dot the navbar's live-round indicator uses
    (.t3g-live-dot, defined in navbar.css, loaded globally) so the "this
    round is still in progress" cue looks identical everywhere it shows
    up."""
    return html.Span(
        [html.Span(className="t3g-live-dot"), html.Span("LIVE")],
        className="t3g-live-badge",
    )


def format_handicap(handicap):
    """A negative stored handicap means a "plus" handicapper (better than
    scratch) -- shown with a leading "+", same convention as a real
    scorecard. Net = gross - handicap handles both cases uniformly
    (subtracting a negative number adds strokes back)."""
    if handicap is None:
        return "—"
    return f"+{abs(round(handicap))}" if handicap < 0 else f"{round(handicap)}"