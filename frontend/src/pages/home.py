# target path: frontend/src/pages/home.py (full replacement)
"""
Home is now the activity feed -- everything that used to live here
(live rounds, pending invites, the Handicap Index panel, Your Clubs,
Start New Round / Create Club) moved to the new "My Profile" tab under
My Account (see frontend/src/pages/my_profile.py, a near-verbatim copy
of the old home.py). This file is a fresh, small page built around one
call to GET /players/{player_id}/feed (see list_home_feed_posts's own
docstring in backend/services/round_posts.py for exactly what that
mixes together): every round you or a friend played, plus every post
from every club you belong to.

Round-post cards ('scorecard' type) are the one genuinely new card shape
-- see _feed_round_post_card. A solo round has nothing to page between
(there's only one player, so "the group" and "the detail" are the same
information) and shows its full hole-by-hole breakdown immediately. A
multiplayer round shows the group scorecard first, with prev/next
arrows to page across to your own detailed scorecard (putts, fairways)
plus any handicap change, when you were one of the players -- a
friend's round you didn't play in only ever shows the group view, since
there's no personal detail of yours to page to. That detailed scorecard
renders front 9 stacked above back 9 (see _feed_detail_table) rather
than all 18 holes in one wide row, so it fits a feed card without
horizontal scrolling. Any photos on the round render as a swipeable
carousel (see _feed_round_post_card's photo_gallery, and
assets/round_photo_carousel.js for the swipe/counter/dot behavior)
rather than a stacked list.

The other three post types (join/tournament/manual) reuse the exact
same rendering club.py's own Feed tab already uses -- duplicated here,
not imported, same "small per-page copies" convention as everywhere
else in this app -- with one addition: since this feed mixes posts from
every club you're in together (unlike a club's own Feed tab, which only
ever shows one club), each one is tagged with which club it came from.
"""
import base64
from datetime import datetime

import dash
import requests
from dash import MATCH, Input, Output, State, callback, dcc, html
from flask import session

from config import API_BASE_URL

dash.register_page(__name__, path="/", name="Home")


def _feed_avatar(name, photo_url):
    """Same initials-or-photo circle as every leaderboard/feed card
    elsewhere in this app -- duplicated here rather than imported, same
    convention club.py's own _leaderboard_avatar already follows."""
    if photo_url:
        return html.Img(src=photo_url, className="t3g-leaderboard-avatar t3g-leaderboard-avatar--photo")
    words = (name or "").split()
    initials = "".join(w[0] for w in words[:2] if w).upper() or "?"
    return html.Span(initials, className="t3g-leaderboard-avatar")


def _format_feed_timestamp(iso_str):
    """"D Mon YYYY, HH:MM" -- see club.py's own _format_feed_timestamp
    for why the day-of-month is built by hand instead of via strftime's
    platform-dependent %-d/%#d."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return iso_str
    return f"{dt.day} {dt.strftime('%b %Y, %H:%M')}"


def _club_tag(post):
    """Small "posted in {club}" link -- only ever present on a post
    sourced from one of your clubs (see list_home_feed_posts, which
    attaches club_name/club_slug only in that branch); a round post
    that's just "you or a friend played this" has neither and gets no
    tag at all."""
    if not post.get("club_slug"):
        return None
    return dcc.Link(
        f"in {post.get('club_name') or 'a club'}",
        href=f"/clubs/{post['club_slug']}",
        className="t3g-feed-post-club-tag",
    )


def _feed_post_header(name, photo_url, timestamp_text, post):
    return html.Div(
        className="t3g-feed-post-header",
        children=[
            _feed_avatar(name, photo_url),
            html.Div(
                className="t3g-feed-post-header-text",
                children=[
                    html.Span(name, className="t3g-feed-post-author"),
                    html.Div(
                        className="t3g-feed-post-timestamp-row",
                        children=[html.Span(timestamp_text, className="t3g-feed-post-timestamp"), _club_tag(post)],
                    ),
                ],
            ),
        ],
    )


def _handicap_delta_badge(change):
    """{"before": x, "after": y} -> a small colored badge -- green/down
    for an improved (lower) Handicap Index, amber/up if it went the other
    way. None (no change, or a solo round which never gets one at all --
    see create_round_post's docstring) renders nothing."""
    if not change or change.get("before") is None or change.get("after") is None:
        return None
    before, after = change["before"], change["after"]
    improved = after < before
    arrow = "▼" if improved else "▲"
    css_class = "t3g-feed-handicap-badge t3g-feed-handicap-badge--down" if improved else "t3g-feed-handicap-badge t3g-feed-handicap-badge--up"
    return html.Span(f"Handicap {before:.1f} {arrow} {after:.1f}", className=css_class)


def _score_mark(strokes, par, nr):
    """One hole's score, in the same circle/square shorthand a paper
    scorecard uses -- a filled circle for eagle-or-better, an outlined
    circle for birdie, plain text for par, an outlined square for
    bogey, and a filled square for double-bogey-or-worse. Needs both
    strokes and a real (non-NR) par to classify at all; an unplayed
    hole or one marked NR just shows a dash/label with no shape."""
    if nr:
        return html.Span("NR", className="t3g-score-mark")
    if strokes is None:
        return html.Span("—", className="t3g-score-mark")
    if not par:
        return html.Span(str(strokes), className="t3g-score-mark")

    diff = strokes - par
    if diff <= -2:
        modifier = "eagle"
    elif diff == -1:
        modifier = "birdie"
    elif diff == 0:
        modifier = "par"
    elif diff == 1:
        modifier = "bogey"
    else:
        modifier = "double-bogey-plus"

    return html.Span(str(strokes), className=f"t3g-score-mark t3g-score-mark--{modifier}")


def _feed_hole_table(holes, subtotal_label, out_in_total):
    """One half (front 9 or back 9) of a player's hole-by-hole
    breakdown -- just Hole/Par/Score, 9 holes across plus a subtotal
    column, rather than the old single 18-column table with Putts/FIR
    rows too (that level of detail is still in the API response --
    _detailed_player_scorecard in round_posts.py -- just not shown on
    this compact feed card anymore). See _feed_detail_table below for
    why it's split front-9-over-back-9 instead of all 18 in one row.
    The Hole/Par rows sit in a <thead> and the Score row in a <tbody>
    specifically so home.css can tint them differently (see
    .t3g-feed-detail-table thead) -- a real scorecard's hole/par line
    reads as the "fixed" part and the score line as the "played" part,
    and the two rows getting different backgrounds makes that reading
    order obvious at a glance."""
    header = html.Tr(
        [html.Th("Hole")] + [html.Th(str(h["hole_number"])) for h in holes] + [html.Th(subtotal_label)]
    )
    par_row = html.Tr(
        [html.Td("Par", className="t3g-feed-detail-row-label")]
        + [html.Td(h.get("par") if h.get("par") is not None else "—") for h in holes]
        + [html.Td(out_in_total["par"] if out_in_total["par"] is not None else "—")]
    )
    score_row = html.Tr(
        [html.Td("Score", className="t3g-feed-detail-row-label")]
        + [html.Td(_score_mark(h.get("strokes"), h.get("par"), h.get("nr"))) for h in holes]
        + [
            html.Td(
                out_in_total["strokes"] if out_in_total["strokes"] is not None else "—",
                className="t3g-feed-detail-total-cell",
            )
        ]
    )
    return html.Table(
        className="t3g-feed-detail-table",
        children=[html.Thead([header, par_row]), html.Tbody([score_row])],
    )


def _feed_hole_subtotals(holes):
    """Par/strokes totals for one half (front 9 or back 9) of a round --
    the Out/In column each _feed_hole_table ends with."""
    played = [h for h in holes if h.get("strokes") is not None]
    return {
        "par": sum(h["par"] for h in holes if h.get("par") is not None) or None,
        "strokes": sum(h["strokes"] for h in played) if played else None,
    }


def _feed_detail_table(detail):
    """One player's hole-by-hole breakdown, front 9 stacked above back
    9 rather than all 18 holes across in one row -- 18 columns plus a
    Hole label and a totals column never fit a phone width, which used
    to force this table into its own horizontal scroll
    (.t3g-feed-detail-table-wrap's old overflow-x: auto). Two 9-wide
    tables both fit without scrolling instead, the same OUT/IN split a
    real scorecard uses, just stacked rather than side by side (see
    analysis.py's full scorecard for the side-by-side version -- that
    one's fine with a horizontal scroll since it's a deliberate,
    single-round detail page rather than a feed card). Deliberately
    just Hole/Par/Score -- Putts and FIR made this card busier than a
    quick feed glance needs; that detail's still available via the full
    scorecard on analysis.py for anyone who wants it."""
    holes = detail.get("holes", [])
    front = [h for h in holes if h.get("hole_number") and h["hole_number"] <= 9]
    back = [h for h in holes if h.get("hole_number") and h["hole_number"] > 9]

    tables = []
    if front:
        tables.append(_feed_hole_table(front, "Out", _feed_hole_subtotals(front)))
    if back:
        tables.append(_feed_hole_table(back, "In", _feed_hole_subtotals(back)))

    return html.Div(className="t3g-feed-detail-table-wrap", children=tables)


def _feed_group_view(post):
    scorecard = post.get("scorecard")
    if not scorecard:
        return html.P("This round's scorecard is no longer available.", className="t3g-empty-state")
    rows = [
        html.Div(
            className="t3g-feed-scorecard-row",
            children=[
                html.Span(p["name"], className="t3g-feed-scorecard-name"),
                html.Span(
                    str(p["total_strokes"]) if p["thru"] == 18 else f"thru {p['thru']}",
                    className="t3g-feed-scorecard-score",
                ),
            ],
        )
        for p in scorecard["players"]
    ]
    return html.Div(rows, className="t3g-feed-scorecard")


def _feed_detail_view(post):
    detail = post.get("viewer_detail")
    if not detail:
        return _feed_group_view(post)
    children = [_feed_detail_table(detail)]
    badge = _handicap_delta_badge(post.get("viewer_handicap_change"))
    if badge:
        children.append(badge)
    return html.Div(children, className="t3g-feed-detail-view")


def _feed_round_body(post, view):
    return _feed_detail_view(post) if view == "detail" else _feed_group_view(post)


def _format_score_to_par(value):
    """Standard golf shorthand -- "E" for level par, a leading + for
    over, a bare - for under (Python's own str() of a negative int
    already has the minus sign)."""
    if value is None:
        return "—"
    if value == 0:
        return "E"
    return f"+{value}" if value > 0 else str(value)


def _feed_stats_slide(detail):
    """An auto-generated extra "photo" -- a 2x2 grid of round-summary
    stats that swipes in the same carousel as any real photos on the
    post (see _feed_round_post_card), always first since a round has
    these numbers the moment it posts, before anyone's necessarily
    added a real photo yet. Score to Par carries Net (this player's
    handicap-adjusted score to par) as a smaller line underneath and
    Stableford points as a superscript next to the headline number --
    see _round_scoring_stats in round_posts.py for exactly how each
    stat here (score_to_par, net_score_to_par, stableford_points,
    gir_hit/gir_eligible) is derived; fairways_hit/fairways_eligible and
    total_putts were already part of the detail payload."""
    score_row_children = [html.Span(_format_score_to_par(detail.get("score_to_par")), className="t3g-stat-value")]
    if detail.get("stableford_points") is not None:
        score_row_children.append(
            html.Span(f"{detail['stableford_points']}pts", className="t3g-stat-superscript")
        )

    score_tile_children = [
        html.Span("Score to Par", className="t3g-stat-label"),
        html.Div(score_row_children, className="t3g-stat-value-row"),
    ]
    if detail.get("net_score_to_par") is not None:
        score_tile_children.append(
            html.Span(f"Net {_format_score_to_par(detail['net_score_to_par'])}", className="t3g-stat-subvalue")
        )

    fairways_hit, fairways_eligible = detail.get("fairways_hit"), detail.get("fairways_eligible")
    putts = detail.get("total_putts")
    gir_hit, gir_eligible = detail.get("gir_hit"), detail.get("gir_eligible")
    gir_pct = round(100 * gir_hit / gir_eligible) if gir_eligible else None

    tiles = [
        html.Div(score_tile_children, className="t3g-stat-tile"),
        html.Div(
            [
                html.Span("Fairways", className="t3g-stat-label"),
                html.Span(
                    f"{fairways_hit}/{fairways_eligible}" if fairways_eligible is not None else "—",
                    className="t3g-stat-value",
                ),
            ],
            className="t3g-stat-tile",
        ),
        html.Div(
            [
                html.Span("Putts", className="t3g-stat-label"),
                html.Span(str(putts) if putts is not None else "—", className="t3g-stat-value"),
            ],
            className="t3g-stat-tile",
        ),
        html.Div(
            [
                html.Span("GIR", className="t3g-stat-label"),
                html.Span(f"{gir_pct}%" if gir_pct is not None else "—", className="t3g-stat-value"),
            ],
            className="t3g-stat-tile",
        ),
    ]

    return html.Div(html.Div(tiles, className="t3g-stat-grid"), className="t3g-feed-stats-slide")


def _feed_primary_player_display(players):
    """"{name} played a round" for a solo round, or "{primary} and N
    others played a round" for a multiplayer one -- replaces listing
    every player's name in full (see is_owner on each summary player,
    set in _group_scorecard_summary in round_posts.py). The primary
    player is whoever started the round (falls back to the first player
    in the list on the off chance no one's flagged as owner, which
    shouldn't normally happen). Same wording for every viewer regardless
    of which of the round's players happen to be their friends -- not
    personalized per viewer, since that would need a friends-list lookup
    on every feed load just to decide whose name to show."""
    if not players:
        return "A round was played"
    primary = next((p for p in players if p.get("is_owner")), players[0])
    if len(players) == 1:
        return f"{primary['name']} played a round"
    others = len(players) - 1
    return f"{primary['name']} and {others} other{'s' if others != 1 else ''} played a round"


def _feed_photo_composer(round_id, can_add_photo):
    """The only place a photo can still be attached to a feed post -- see
    club.py's _feed_composer for why manual posts lost their own upload
    control. multiple=True here (unlike every other single-file
    dcc.Upload in this app -- profile picture, club photo, manual post)
    since a round can carry as many photos as its players want to add,
    with no cap in add_round_post_photo; selecting several at once in
    the file picker is the deliberate "attach as many as you like"
    affordance, on top of the fact that re-opening the picker to add
    more later still works too."""
    if not can_add_photo:
        return None
    return html.Div(
        className="t3g-feed-photo-composer",
        children=[
            dcc.Upload(
                id={"type": "feed-photo-upload", "round_id": round_id},
                children=html.Button(
                    "Add Photos", className="t3g-panel-action-button t3g-panel-action-button--secondary"
                ),
                accept="image/*",
                multiple=True,
                style={"display": "inline-block"},
            ),
            html.Div(id={"type": "feed-photo-error", "round_id": round_id}, className="text-danger mt-2"),
        ],
    )


def _feed_round_post_card(post, player_id):
    """A completed round's post -- see create_round_post's docstring in
    backend/services/round_posts.py for exactly when this gets created.
    The name/course/date header always stays fixed above the carousel;
    everything else about the round -- the scorecard, the stats, any
    photos -- is now one swipeable carousel below it (see
    _feed_stats_slide and home.css) rather than the scorecard sitting
    permanently visible with photos in a separate strip underneath.
    Slide 1 is always the scorecard (the solo hole-by-hole table, or for
    a multiplayer round, the same group-scorecard/personal-detail toggle
    this always had -- the prev/next arrows still work exactly as
    before, just inside the carousel's first slide instead of above it).
    Slide 2 is the stats card, when this viewer has a detail payload to
    build one from (own round, or a multiplayer round they played in) --
    skipped for a friend's round they didn't play in, since there's no
    personal detail of theirs to summarize. Any real photos follow
    after that."""
    round_id = post["round_id"]
    scorecard = post.get("scorecard") or {}
    course_bits = [b for b in [scorecard.get("club_name"), scorecard.get("course_name")] if b]
    course_text = " – ".join(course_bits)
    timestamp_text = _format_feed_timestamp(post.get("created_at"))
    round_header_text = _feed_primary_player_display(scorecard.get("players", []))
    player_ids = (post.get("metadata") or {}).get("player_ids", [])
    can_add_photo = player_id in player_ids

    if not post.get("is_multiplayer") and post.get("solo_detail"):
        body = html.Div(
            [_feed_detail_table(post["solo_detail"])]
            + ([_handicap_delta_badge(post.get("viewer_handicap_change"))] if post.get("viewer_handicap_change") else []),
            className="t3g-feed-detail-view",
        )
        toggle_controls = None
    else:
        has_detail = bool(post.get("viewer_detail"))
        body = html.Div(
            id={"type": "feed-round-body", "round_id": round_id},
            children=_feed_round_body(post, "group"),
        )
        toggle_controls = (
            html.Div(
                className="t3g-feed-round-toggle",
                children=[
                    html.Button("‹", id={"type": "feed-round-prev", "round_id": round_id}, className="t3g-feed-round-toggle-arrow", n_clicks=0),
                    html.Span("Scorecard", className="t3g-feed-round-toggle-label"),
                    html.Button("›", id={"type": "feed-round-next", "round_id": round_id}, className="t3g-feed-round-toggle-arrow", n_clicks=0),
                ],
            )
            if has_detail
            else None
        )

    # Slide 1, always -- a completed round always has at least a group
    # scorecard, so this alone guarantees the stats slide right after it
    # is never the first/only thing in the carousel, without needing a
    # separate placeholder slide for the zero-photo case.
    scorecard_slide = html.Div(
        className="t3g-feed-scorecard-slide",
        children=[c for c in [toggle_controls, body] if c is not None],
    )

    photos = post.get("photos") or []
    detail_for_stats = post.get("solo_detail") or post.get("viewer_detail")
    stats_slide = [_feed_stats_slide(detail_for_stats)] if detail_for_stats else []

    # The scroll track itself keeps the same id/className the upload
    # callback already targets (handle_feed_photo_upload just appends
    # more <img> children to it, unchanged) -- everything new here is
    # the outer carousel shell around it. The counter badge and dots are
    # empty placeholders on the Python/Dash side; assets/
    # round_photo_carousel.js fills and updates them from the track's
    # actual scroll position client-side (counting every direct child,
    # not just <img> tags, so the scorecard/stats slides count too),
    # since "which slide is centered right now" isn't state Dash needs
    # to know about on the server.
    photo_gallery = html.Div(
        className="t3g-feed-photo-carousel",
        children=[
            html.Div(
                id={"type": "feed-photo-list", "round_id": round_id},
                className="t3g-feed-photo-gallery",
                children=[scorecard_slide] + stats_slide + [html.Img(src=url, className="t3g-feed-post-image") for url in photos],
            ),
            html.Div(className="t3g-feed-photo-dots"),
        ],
    )

    return html.Div(
        className="t3g-feed-post t3g-feed-post--round",
        children=[
            html.Div(
                className="t3g-feed-post-header",
                children=[
                    html.Span("⛳", className="t3g-feed-post-icon"),
                    html.Div(
                        className="t3g-feed-post-header-text",
                        children=[
                            html.Span(round_header_text, className="t3g-feed-post-author"),
                            html.Div(
                                className="t3g-feed-post-timestamp-row",
                                children=[
                                    html.Span(" – ".join(b for b in [course_text, timestamp_text] if b), className="t3g-feed-post-timestamp"),
                                    _club_tag(post),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            photo_gallery,
            dcc.Store(id={"type": "feed-round-post-store", "round_id": round_id}, data=post),
            dcc.Store(id={"type": "feed-round-view", "round_id": round_id}, data="group"),
            _feed_photo_composer(round_id, can_add_photo),
        ],
    )


def _feed_post_card(post, player_id):
    post_type = post.get("post_type")
    if post_type == "scorecard":
        return _feed_round_post_card(post, player_id)

    timestamp_text = _format_feed_timestamp(post.get("created_at"))
    author_name = post.get("author_name") or "A player"

    if post_type == "join":
        return html.Div(
            className="t3g-feed-post",
            children=[
                _feed_post_header(author_name, post.get("author_photo_url"), timestamp_text, post),
                html.P(f"{author_name} joined the club.", className="t3g-feed-post-body"),
            ],
        )

    if post_type == "tournament":
        metadata = post.get("metadata") or {}
        tournament_name = metadata.get("tournament_name") or "a tournament"
        tournament_id = metadata.get("tournament_id")
        slug = post.get("club_slug")
        body_children = [f"{author_name} created a new tournament: "]
        if tournament_id and slug:
            body_children.append(dcc.Link(tournament_name, href=f"/clubs/{slug}/tournaments/{tournament_id}"))
        else:
            body_children.append(tournament_name)
        return html.Div(
            className="t3g-feed-post",
            children=[
                _feed_post_header(author_name, post.get("author_photo_url"), timestamp_text, post),
                html.P(body_children, className="t3g-feed-post-body"),
            ],
        )

    # "manual"
    body_children = []
    if post.get("body"):
        body_children.append(html.P(post["body"], className="t3g-feed-post-body"))
    if post.get("image_url"):
        body_children.append(html.Img(src=post["image_url"], className="t3g-feed-post-image"))
    return html.Div(
        className="t3g-feed-post",
        children=[_feed_post_header(author_name, post.get("author_photo_url"), timestamp_text, post)] + body_children,
    )


def layout(**kwargs):
    player_id = session.get("player_id")

    if not session.get("logged_in") or not player_id:
        session.clear()
        return dcc.Location(pathname="/signin", id="home-redirect-signin", refresh=True)

    feed_resp = requests.get(f"{API_BASE_URL}/players/{player_id}/feed")
    posts = feed_resp.json() if feed_resp.status_code == 200 else []

    # "home" category notifications (a club post, a photo added to your
    # round) both point back to "/" -- landing here at all is "seen",
    # same best-effort pattern as friends.py's own version of this call.
    try:
        requests.post(f"{API_BASE_URL}/notifications/{player_id}/read/home")
    except requests.RequestException:
        pass

    if posts:
        body = html.Div([_feed_post_card(post, player_id) for post in posts], className="t3g-feed-list")
    else:
        body = html.P(
            "Nothing here yet -- play a round, join a club, or add a friend to start "
            "seeing activity.",
            className="t3g-empty-state",
        )

    return html.Div(
        className="t3g-page t3g-home-feed-page",
        children=[body],
    )


@callback(
    Output({"type": "feed-round-body", "round_id": MATCH}, "children"),
    Output({"type": "feed-round-view", "round_id": MATCH}, "data"),
    Input({"type": "feed-round-prev", "round_id": MATCH}, "n_clicks"),
    Input({"type": "feed-round-next", "round_id": MATCH}, "n_clicks"),
    State({"type": "feed-round-view", "round_id": MATCH}, "data"),
    State({"type": "feed-round-post-store", "round_id": MATCH}, "data"),
    prevent_initial_call=True,
)
def switch_feed_round_view(prev_clicks, next_clicks, current_view, post):
    # Only ever two views to page between, so prev and next do the exact
    # same thing -- flip it -- rather than needing separate branches per
    # direction.
    new_view = "detail" if current_view == "group" else "group"
    return _feed_round_body(post, new_view), new_view


@callback(
    Output({"type": "feed-photo-error", "round_id": MATCH}, "children"),
    Output({"type": "feed-photo-list", "round_id": MATCH}, "children"),
    Input({"type": "feed-photo-upload", "round_id": MATCH}, "contents"),
    State({"type": "feed-photo-upload", "round_id": MATCH}, "filename"),
    State({"type": "feed-photo-list", "round_id": MATCH}, "children"),
    prevent_initial_call=True,
)
def handle_feed_photo_upload(contents_list, filename_list, current_children):
    # multiple=True on the Upload component means both of these arrive
    # as lists (one entry per file picked in a single dialog), even for
    # a single file -- posted one at a time in a plain loop rather than
    # batched server-side, same add_round_post_photo call repeated per
    # file, so one bad file (wrong type, upload failure) doesn't have to
    # take the rest down with it: whatever succeeds still lands in the
    # gallery, and the error line reports only the ones that didn't.
    if not contents_list:
        return "", dash.no_update

    round_id = dash.ctx.triggered_id["round_id"]
    player_id = session.get("player_id")
    filename_list = filename_list or []

    new_photos = []
    failures = []
    for i, contents in enumerate(contents_list):
        filename = filename_list[i] if i < len(filename_list) else None

        header, encoded = contents.split(",", 1)
        file_bytes = base64.b64decode(encoded)
        content_type = header.split(";")[0].replace("data:", "") or "image/jpeg"

        response = requests.post(
            f"{API_BASE_URL}/rounds/{round_id}/post/photo",
            data={"author_id": player_id},
            files={"file": (filename or "photo.jpg", file_bytes, content_type)},
        )

        if response.status_code != 201:
            try:
                detail = response.json().get("detail", "Couldn't add that photo.")
                if not isinstance(detail, str):
                    detail = "Couldn't add that photo."
            except ValueError:
                detail = "Couldn't add that photo."
            failures.append(f"{filename or 'A photo'}: {detail}")
            continue

        new_photos.append(html.Img(src=response.json()["image_url"], className="t3g-feed-post-image"))

    error_text = " ".join(failures)
    children = (current_children or []) + new_photos if new_photos else dash.no_update
    return error_text, children