# target path: frontend/src/components/scorecard.py (full replacement)
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
    blank.

    Tournament-linked rounds (round_data carries tournament_id/
    tournament_name/tournament_round_number -- see _tournament_context_
    for_round in backend/services/rounds.py) get a different label
    entirely: club, tournament name, and round number, instead of course/
    tees. That's the whole point of a tournament round from a player's
    perspective -- which competition and which round of it -- not which
    tee they played off, and it's what actually distinguishes it from a
    casual round wherever this label shows up (the home page's Live Round
    panel, Rounds History, Scoring History), now that a player can have
    one of each in progress at the same time."""
    if round_data.get("tournament_id") and round_data.get("tournament_name"):
        label = round_data.get("club_name") or "Tournament round"
        label += f" — {round_data['tournament_name']}"
        if round_data.get("tournament_round_number"):
            label += f" — Round {round_data['tournament_round_number']}"
    else:
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


def tournament_round_badge():
    """Small tag marking a round as tournament-linked -- shown alongside
    live_badge() for a round in progress, or on its own for a completed
    one, anywhere round_header_label appears. Exists specifically so a
    tournament round is never mistaken for a casual one at a glance, on
    top of round_header_label's own tournament-aware title text -- most
    important on the home page's Live Round panel, since a player can now
    have a casual round *and* a tournament round live at the same time
    (see backend/services/rounds.py's tournament_scope split), shown as
    two separate cards there that need to read as clearly different kinds
    of thing, not just two rounds that happen to have different titles."""
    return html.Span("TOURNAMENT", className="t3g-tournament-round-badge")


def format_handicap(handicap):
    """A negative stored handicap means a "plus" handicapper (better than
    scratch) -- shown with a leading "+", same convention as a real
    scorecard. Net = gross - handicap handles both cases uniformly
    (subtracting a negative number adds strokes back)."""
    if handicap is None:
        return "—"
    return f"+{abs(round(handicap))}" if handicap < 0 else f"{round(handicap)}"