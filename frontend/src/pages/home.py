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
there's no personal detail of yours to page to.

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


def _feed_detail_table(detail):
    """One player's hole-by-hole breakdown -- Hole/Par/Score/Putts/FIR,
    18 holes across, plus a totals row. FIR (fairway in regulation) is
    left blank on a par 3 -- see _detailed_player_scorecard's own
    docstring in round_posts.py for why those holes are excluded from
    the fairway count entirely rather than counted as a miss."""
    holes = detail.get("holes", [])
    header = html.Tr(
        [html.Th("Hole")] + [html.Th(str(h["hole_number"])) for h in holes] + [html.Th("Tot")]
    )
    par_row = html.Tr(
        [html.Td("Par", className="t3g-feed-detail-row-label")]
        + [html.Td(h.get("par") if h.get("par") is not None else "—") for h in holes]
        + [html.Td("")]
    )
    score_row = html.Tr(
        [html.Td("Score", className="t3g-feed-detail-row-label")]
        + [html.Td(h.get("strokes") if h.get("strokes") is not None else "—") for h in holes]
        + [html.Td(detail.get("total_strokes", "—"), className="t3g-feed-detail-total-cell")]
    )
    putts_row = html.Tr(
        [html.Td("Putts", className="t3g-feed-detail-row-label")]
        + [html.Td(h.get("putts") if h.get("putts") is not None else "—") for h in holes]
        + [html.Td(detail.get("total_putts", "—"), className="t3g-feed-detail-total-cell")]
    )
    fairway_row = html.Tr(
        [html.Td("FIR", className="t3g-feed-detail-row-label")]
        + [
            html.Td("—" if not h.get("par") or h["par"] <= 3 else ("✓" if h.get("fairway_hit") else "✗"))
            for h in holes
        ]
        + [html.Td(f"{detail.get('fairways_hit', 0)}/{detail.get('fairways_eligible', 0)}", className="t3g-feed-detail-total-cell")]
    )
    return html.Div(
        className="t3g-feed-detail-table-wrap",
        children=html.Table(
            className="t3g-feed-detail-table",
            children=[html.Thead([header, par_row]), html.Tbody([score_row, putts_row, fairway_row])],
        ),
    )


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


def _feed_photo_composer(round_id, can_add_photo):
    if not can_add_photo:
        return None
    return html.Div(
        className="t3g-feed-photo-composer",
        children=[
            dcc.Upload(
                id={"type": "feed-photo-upload", "round_id": round_id},
                children=html.Button(
                    "Add Photo", className="t3g-panel-action-button t3g-panel-action-button--secondary"
                ),
                accept="image/*",
                style={"display": "inline-block"},
            ),
            html.Div(id={"type": "feed-photo-error", "round_id": round_id}, className="text-danger mt-2"),
        ],
    )


def _feed_round_post_card(post, player_id):
    """A completed round's post -- see create_round_post's docstring in
    backend/services/round_posts.py for exactly when this gets created.
    Solo rounds have no group/detail toggle at all (there's only one
    player, so there's nothing to page between); a multiplayer round you
    played in gets prev/next arrows between the group scorecard and your
    own detail, and a multiplayer round you're only seeing because a
    friend played it (or it matched a shared club) shows just the group
    view, since there's no personal detail of yours to show."""
    round_id = post["round_id"]
    scorecard = post.get("scorecard") or {}
    course_bits = [b for b in [scorecard.get("club_name"), scorecard.get("course_name")] if b]
    course_text = " – ".join(course_bits)
    timestamp_text = _format_feed_timestamp(post.get("created_at"))
    player_names = ", ".join(p["name"] for p in scorecard.get("players", []))
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

    photos = post.get("photos") or []
    photo_gallery = html.Div(
        id={"type": "feed-photo-list", "round_id": round_id},
        className="t3g-feed-photo-gallery",
        children=[html.Img(src=url, className="t3g-feed-post-image") for url in photos],
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
                            html.Span(f"{player_names} played a round", className="t3g-feed-post-author"),
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
            toggle_controls,
            body,
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
def handle_feed_photo_upload(contents, filename, current_children):
    if not contents:
        return "", dash.no_update

    round_id = dash.ctx.triggered_id["round_id"]
    player_id = session.get("player_id")

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
            detail = response.json().get("detail", "Couldn't add that photo. Try again.")
            if not isinstance(detail, str):
                detail = "Couldn't add that photo. Try again."
        except ValueError:
            detail = "Couldn't add that photo. Try again."
        return detail, dash.no_update

    new_photo = html.Img(src=response.json()["image_url"], className="t3g-feed-post-image")
    return "", (current_children or []) + [new_photo]