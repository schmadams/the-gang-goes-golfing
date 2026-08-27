# target path: backend/services/round_posts.py (new file)
import time

from backend.database import supabase
from backend.services.friends import list_friends
from backend.services.notifications import create_notification
from backend.services.storage import extension_for, upload_image

ROUND_POST_PHOTO_BUCKET = "round-post-photos"


class NotRoundPostPlayerError(Exception):
    """Raised when someone who didn't play in a round tries to add a
    photo to its post."""


class RoundPostNotFoundError(Exception):
    """Raised when a round_id has no round_posts row yet -- shouldn't
    normally happen for a completed round (create_round_post runs
    best-effort right when it completes), but a round from before this
    feature existed, or one whose post creation failed, has nowhere to
    attach a retroactive photo."""


def _get_round_post(round_id: str) -> dict | None:
    response = (
        supabase
        .table("round_posts")
        .select("*")
        .eq("round_id", round_id)
        .maybe_single()
        .execute()
    )
    return response.data if response is not None else None


def _compute_shared_club_ids(player_ids: list[str]) -> list[str]:
    """Every club where *all* of these players happen to share membership
    -- not just a club the round was explicitly tagged to. Same "any
    shared club membership" rule the club feed used before this feature
    existed (see the old create_scorecard_posts), just computed once here
    instead of written straight into club_posts -- a club's Feed tab now
    matches against this list at read time (see list_round_posts_for_club)
    rather than getting its own separate copy of the post."""
    if len(player_ids) < 2:
        return []

    # Local import: club_players.py doesn't import this module, so this
    # would be safe at module level too -- kept local anyway purely for
    # symmetry with this file's other local imports below, which *do*
    # need to be local (genuine circular-import breaks).
    from backend.services.club_players import list_clubs_for_player

    club_id_sets = [
        {row["club_id"] for row in list_clubs_for_player(player_id)}
        for player_id in player_ids
    ]
    shared = set.intersection(*club_id_sets) if club_id_sets else set()
    return list(shared)


def create_round_post(round_id: str, player_ids: list[str], handicap_changes: dict | None = None) -> dict | None:
    """Creates the one-and-only post for a round, the moment it reaches
    status='completed' -- see finish_round (solo rounds go straight to
    completed) and sign_off_round (a multiplayer round only gets here
    once every accepted player has signed off) in backend/services/
    rounds.py, both of which call this best-effort so a feed post failing
    can never block the round itself from finishing.

    round_id is unique on this table -- if a post already exists (e.g. a
    stale double-call, though both call sites are themselves idempotent),
    this returns the existing row rather than erroring or creating a
    second one."""
    existing = _get_round_post(round_id)
    if existing:
        return existing

    metadata = {
        "player_ids": player_ids,
        "shared_club_ids": _compute_shared_club_ids(player_ids),
        "handicap_changes": handicap_changes or {},
    }
    response = (
        supabase
        .table("round_posts")
        .insert({"round_id": round_id, "metadata": metadata})
        .execute()
    )
    return response.data[0] if response.data else None


def add_round_post_photo(
    round_id: str,
    author_id: str,
    file_bytes: bytes,
    filename: str | None,
    content_type: str | None,
) -> dict:
    """Adds one more photo to an already-posted round -- deliberately
    open to any of the round's own players any time after the fact (not
    just whoever owns the round, and not just right after it's posted),
    since "someone adds their photos from the round a week later" is
    exactly the retroactive case this is for. Gated on round membership
    the same way every other round mutation in this app already is,
    rather than on club membership (a round post has no single club to
    check against -- see create_round_post's shared_club_ids, which can
    be empty, one, or several)."""
    round_post = _get_round_post(round_id)
    if not round_post:
        raise RoundPostNotFoundError("This round doesn't have a post to add a photo to.")

    player_ids = (round_post.get("metadata") or {}).get("player_ids", [])
    if author_id not in player_ids:
        raise NotRoundPostPlayerError("Only players in this round can add photos to it.")

    storage_path = f"{round_id}/{author_id}-{int(time.time() * 1000)}{extension_for(filename)}"
    image_url = upload_image(ROUND_POST_PHOTO_BUCKET, storage_path, file_bytes, content_type)

    response = (
        supabase
        .table("round_post_photos")
        .insert({"round_id": round_id, "author_id": author_id, "image_url": image_url})
        .execute()
    )
    photo = response.data[0]

    # Best-effort, same convention as every other create_notification call
    # site -- every other player in the round gets one, not the uploader
    # themself.
    try:
        author_response = (
            supabase.table("players").select("first_name, surname, nickname").eq("id", author_id).maybe_single().execute()
        )
        author = (author_response.data if author_response is not None else None) or {}
        author_name = (
            author.get("nickname")
            or f"{author.get('first_name', '')} {author.get('surname', '')}".strip()
            or "Someone"
        )
        for other_player_id in player_ids:
            if other_player_id == author_id:
                continue
            create_notification(
                other_player_id,
                "home",
                f"{author_name} added a photo to your round",
                url="/",
            )
    except Exception as exc:
        print(f"[NOTIFY] Failed to notify round {round_id} players of new photo: {exc}")

    return photo


def _list_round_post_photos(round_id: str) -> list[dict]:
    response = (
        supabase
        .table("round_post_photos")
        .select("*")
        .eq("round_id", round_id)
        .order("created_at")
        .execute()
    )
    return response.data or []


def _player_display_name(player: dict) -> str:
    return (
        player.get("nickname")
        or f"{player.get('first_name', '')} {player.get('surname', '')}".strip()
        or "Unknown player"
    )


def _group_scorecard_summary(round_data: dict, player_ids: list[str]) -> dict:
    """The "who played, what did they shoot" summary every round-post
    card shows first -- one line per player, no per-hole detail. Built
    fresh from get_round(...) rather than freezing a copy into
    round_posts.metadata at creation time, so an edit to a not-yet-
    completed... well, completed rounds can't be edited anyway (see
    RoundNotEditableError), but this keeps the post always reflecting
    whatever get_round actually says regardless."""
    players_by_id = {p["player_id"]: p for p in round_data.get("players", [])}
    summary_players = []
    for player_id in player_ids:
        player = players_by_id.get(player_id)
        if not player:
            continue
        total_strokes = sum(h["strokes"] for h in player["holes"] if h.get("strokes") is not None)
        thru = sum(1 for h in player["holes"] if h.get("strokes") is not None)
        summary_players.append({
            "player_id": player_id,
            "name": _player_display_name(player),
            "total_strokes": total_strokes,
            "thru": thru,
        })
    return {
        "round_id": round_data.get("id"),
        "club_name": round_data.get("club_name"),
        "course_name": round_data.get("course_name"),
        "round_date": round_data.get("round_date"),
        "players": summary_players,
    }


def _detailed_player_scorecard(round_data: dict, player_id: str) -> dict | None:
    """The single player's own hole-by-hole breakdown (putts, fairways
    hit) that a round post pages across to from the group summary --
    "the extra detailed scorecard" the solo case posts immediately with,
    since a solo round has no group view to show first."""
    player = next((p for p in round_data.get("players", []) if p["player_id"] == player_id), None)
    if not player:
        return None

    holes = [
        {
            "hole_number": h.get("hole_number"),
            "par": h.get("par") or h.get("manual_par"),
            "strokes": h.get("strokes"),
            "putts": h.get("putts"),
            "fairway_hit": h.get("fairway_hit"),
            "nr": h.get("nr"),
        }
        for h in player.get("holes", [])
    ]
    played_holes = [h for h in holes if h["strokes"] is not None]
    # Fairway hit only applies to par 4s/5s -- a par 3 tee shot has
    # nothing to "hit" in the fairway-in-regulation sense, so it's
    # excluded from both the numerator and denominator here rather than
    # counted as a miss.
    fairway_eligible = [h for h in played_holes if h["par"] and h["par"] > 3]

    return {
        "player_id": player_id,
        "name": _player_display_name(player),
        "holes": holes,
        "total_strokes": sum(h["strokes"] for h in played_holes),
        "total_putts": sum(h["putts"] for h in played_holes if h.get("putts") is not None),
        "fairways_hit": sum(1 for h in fairway_eligible if h.get("fairway_hit")),
        "fairways_eligible": len(fairway_eligible),
        "thru": len(played_holes),
    }


def hydrate_round_post_card(row: dict, viewer_player_id: str | None = None) -> dict:
    """Builds one feed-ready card from a round_posts row -- shared by
    both the home feed (list_home_feed_posts) and the club feed
    (club_posts.get_club_feed, for the round posts that match one of its
    shared_club_ids), so the two surfaces render the exact same round the
    exact same way rather than drifting.

    Always carries the group summary and any photos, same as before this
    feature existed. Only additionally carries `viewer_detail` (this
    specific viewer's own hole-by-hole scorecard + handicap change) when
    viewer_player_id is given and is actually one of the round's players
    -- the club feed has no single "viewer" to personalize for (every
    member sees the same club page), so it calls this with
    viewer_player_id=None and gets just the group view, exactly like a
    'scorecard' club post always has."""
    from backend.services.rounds import get_round

    round_id = row["round_id"]
    metadata = row.get("metadata") or {}
    player_ids = metadata.get("player_ids", [])
    round_data = get_round(round_id)

    is_multiplayer = len(player_ids) > 1

    card = {
        "post_type": "scorecard",
        "round_id": round_id,
        "created_at": row["created_at"],
        "metadata": {"round_id": round_id, "player_ids": player_ids},
        "author_name": None,
        "author_photo_url": None,
        "is_multiplayer": is_multiplayer,
        "photos": [p["image_url"] for p in _list_round_post_photos(round_id)],
        "scorecard": _group_scorecard_summary(round_data, player_ids) if round_data else None,
    }

    if not is_multiplayer and round_data and player_ids:
        # A solo round has no group-vs-personal distinction to protect --
        # there's only one player, so "the group scorecard" and "that
        # player's own detail" are the same information. Posted with the
        # full detail already attached (not viewer-gated the way a
        # multiplayer round's is below), matching "posts immediately with
        # the extra detailed scorecard" for the solo case.
        card["solo_detail"] = _detailed_player_scorecard(round_data, player_ids[0])
    elif viewer_player_id and viewer_player_id in player_ids and round_data:
        card["viewer_detail"] = _detailed_player_scorecard(round_data, viewer_player_id)
        card["viewer_handicap_change"] = metadata.get("handicap_changes", {}).get(viewer_player_id)

    return card


def list_round_posts_for_club(club_id: str) -> list[dict]:
    """Every round post that matches this club's shared membership --
    what the club Feed tab's group-scorecard cards are now sourced from,
    replacing the old club_posts post_type='scorecard' rows one-for-one.
    Fetches recent round_posts and filters in Python rather than a jsonb
    containment query -- this app's whole scale (see whs.py's own PCC
    docstring) is small enough that this is simpler and just as fast in
    practice."""
    rows = (
        supabase
        .table("round_posts")
        .select("*")
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    ).data or []

    matching = [row for row in rows if club_id in (row.get("metadata") or {}).get("shared_club_ids", [])]
    return [hydrate_round_post_card(row) for row in matching]


def list_home_feed_posts(player_id: str, limit: int = 40) -> list[dict]:
    """The personal home feed: every round you or a friend played (with
    your own detailed scorecard/handicap change folded in when it's your
    round), plus every post from every club you're a member of --
    including that club's own round posts, which is why round posts
    shared with a club are de-duplicated by round_id here rather than
    shown once per club they happen to also match."""
    # Local imports: both modules import backend.services.rounds (or, for
    # club_posts, this module indirectly via list_round_posts_for_club),
    # so importing either at module scope here risks a real circular
    # import depending on load order -- deferring avoids it regardless of
    # which module happens to import first.
    from backend.services.club_players import list_clubs_for_player
    from backend.services.club_posts import get_club_feed

    friend_ids = {f["player_id"] for f in list_friends(player_id)}
    relevant_ids = {player_id} | friend_ids

    round_rows = (
        supabase
        .table("round_posts")
        .select("*")
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    ).data or []

    seen_round_ids = set()
    posts = []

    for row in round_rows:
        row_player_ids = (row.get("metadata") or {}).get("player_ids", [])
        if not (set(row_player_ids) & relevant_ids):
            continue
        # Only personalize with this viewer's own detail/handicap change
        # when they actually played it -- a friend's round still shows
        # the group scorecard, just without your own per-hole breakdown
        # tacked on (you weren't there to have one).
        viewer_id = player_id if player_id in row_player_ids else None
        posts.append(hydrate_round_post_card(row, viewer_player_id=viewer_id))
        seen_round_ids.add(row["round_id"])

    # Own home feed mixes posts from every club you're in together, unlike
    # a club's own Feed tab (which only ever shows one club at a time and
    # so never needed to say which club a post came from) -- each club-
    # sourced post below gets that club's name/slug attached so the card
    # can say "posted in {club}" and link back to it.
    my_clubs = {c["club_id"]: (c.get("clubs") or {}) for c in list_clubs_for_player(player_id)}
    for club_id, club in my_clubs.items():
        for post in get_club_feed(club_id):
            if post.get("post_type") == "scorecard":
                # Any round where you're actually a player is already
                # caught (with your own detail attached) by the loop
                # above, since relevant_ids always includes player_id --
                # this branch only ever adds rounds you're *not* in, from
                # a club you share with people who did play them.
                round_id = post.get("round_id") or (post.get("metadata") or {}).get("round_id")
                if round_id in seen_round_ids:
                    continue
                seen_round_ids.add(round_id)
            post["club_name"] = club.get("name")
            post["club_slug"] = club.get("slug")
            posts.append(post)

    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return posts[:limit]