# target path: frontend/src/components/scorecard.py (full replacement)
"""
Shared building blocks for anything that renders a round as a mini
traditional scorecard -- currently the home page's Rounds History panel
(compact), the Scoring History page (detailed), and the sign-off review
page. Kept here instead of duplicated across pages so the birdie/bogey
mark logic and round header formatting only exist once.
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


def pending_signoff_badge():
    """Marks a round as status='pending_signoff' -- finished being played,
    but not yet accepted into anyone's history/handicap because not every
    player has approved the final scorecard yet (see add_round_signoff.sql
    / backend/services/rounds.py finish_round + sign_off_round). Shown
    anywhere round_header_label appears for a round in this state -- the
    Rounds History panel, Scoring History, and the sign-off review page's
    own cards -- so it never looks like just another quietly-completed
    round. Deliberately not red/pulsing like live_badge() -- this isn't
    "in progress right now", it's "waiting on people", a calmer, more
    static kind of pending."""
    return html.Span("PENDING SIGN-OFF", className="t3g-pending-signoff-badge")


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


def player_signoff_status_badge(signed_off_at):
    """Per-player approval status within a round awaiting sign-off --
    used on the sign-off review page (pages/round_signoff.css.py) to show, at
    a glance, who's already approved a scorecard and who's still holding
    it up. Not used anywhere sign-off doesn't apply (a solo round, or a
    round that's already fully completed) -- those never carry this."""
    if signed_off_at:
        return html.Span(
            [html.Span("✓", className="t3g-signoff-status-icon"), html.Span("Signed off")],
            className="t3g-signoff-status-badge t3g-signoff-status-badge--done",
        )
    return html.Span(
        "Awaiting",
        className="t3g-signoff-status-badge t3g-signoff-status-badge--pending",
    )


def format_handicap(handicap):
    """A negative stored handicap means a "plus" handicapper (better than
    scratch) -- shown with a leading "+", same convention as a real
    scorecard. Net = gross - handicap handles both cases uniformly
    (subtracting a negative number adds strokes back)."""
    if handicap is None:
        return "—"
    return f"+{abs(round(handicap))}" if handicap < 0 else f"{round(handicap)}"