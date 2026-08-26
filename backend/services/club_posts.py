# target path: backend/services/club_posts.py (new file)
import time

from backend.database import supabase
from backend.services.storage import extension_for, upload_image

CLUB_POST_PHOTO_BUCKET = "club-post-photos"

# club_posts.author_id has exactly one FK onto players (unlike
# club_invites' two), so a plain unqualified embed is unambiguous --
# same reasoning as club_players.list_players_in_club's own players(*)
# embed. None for a scorecard post (no single author -- see
# create_scorecard_posts), which is why every read of this embed below
# guards on `if author` first.
_AUTHOR_EMBED = "players(id, first_name, surname, nickname, profile_picture_url)"


class NotClubMemberError(Exception):
    """Raised when someone who isn't a member of the club tries to post
    to its feed."""


def _is_club_member(club_id: str, player_id: str) -> bool:
    response = (
        supabase
        .table("club_players")
        .select("club_id")
        .eq("club_id", club_id)
        .eq("player_id", player_id)
        .maybe_single()
        .execute()
    )
    return bool(response is not None and response.data)


def create_manual_post(
    club_id: str,
    author_id: str,
    body: str | None,
    file_bytes: bytes | None,
    filename: str | None,
    content_type: str | None,
) -> dict:
    """A member-authored update on the club's feed -- text, a photo, or
    both (the router requires at least one of the two before this is
    ever called). Any club member can post here, not just the admin --
    unlike Invite/Club Photo/Create Tournament, which stay admin-only
    actions gathered under the Admin tab, the feed is meant to feel like
    a shared space every member can contribute to, not another
    announcement channel.

    Reuses the same storage.upload_image/extension_for helper the
    profile-picture and club-photo uploads already share -- unlike those
    two, though, each post's photo needs its own unique storage path
    (a member can post more than one photo over time; player/club photos
    are always exactly one file that a fresh upload should overwrite),
    so the path includes a millisecond timestamp rather than being keyed
    purely on club_id/author_id."""
    if not _is_club_member(club_id, author_id):
        raise NotClubMemberError("Only members of this club can post to its feed.")

    image_url = None
    if file_bytes:
        storage_path = f"{club_id}/{author_id}-{int(time.time() * 1000)}{extension_for(filename)}"
        image_url = upload_image(CLUB_POST_PHOTO_BUCKET, storage_path, file_bytes, content_type)

    response = (
        supabase
        .table("club_posts")
        .insert({
            "club_id": club_id,
            "post_type": "manual",
            "author_id": author_id,
            "body": body,
            "image_url": image_url,
        })
        .execute()
    )
    return response.data[0]


def create_join_post(club_id: str, player_id: str) -> dict:
    """Automated post whenever add_player_to_club actually adds someone
    to a club -- covers both the invite-acceptance path (respond_to_
    club_invite) and a brand-new club's creator auto-joining
    (create_club) the same way add_player_to_club itself doesn't
    special-case either caller. Best-effort from both call sites (wrapped
    in try/except there) -- a feed post failing should never be able to
    block someone actually joining a club."""
    response = (
        supabase
        .table("club_posts")
        .insert({
            "club_id": club_id,
            "post_type": "join",
            "author_id": player_id,
        })
        .execute()
    )
    return response.data[0]


def create_tournament_post(club_id: str, tournament_id: str, tournament_name: str, admin_id: str) -> dict:
    """Automated post whenever create_tournament succeeds -- authored by
    the admin who created it, with the tournament's id/name carried in
    metadata so the feed card can link straight through to it without a
    second lookup. Best-effort from its call site, same reasoning as
    create_join_post -- a feed post failing should never block the
    tournament itself from being created."""
    response = (
        supabase
        .table("club_posts")
        .insert({
            "club_id": club_id,
            "post_type": "tournament",
            "author_id": admin_id,
            "metadata": {"tournament_id": tournament_id, "tournament_name": tournament_name},
        })
        .execute()
    )
    return response.data[0]


def create_scorecard_posts(round_id: str, player_ids: list[str]) -> list[dict]:
    """Automated post(s) whenever a multiplayer round reaches status=
    completed -- see sign_off_round in backend/services/rounds.py, the
    only place that actually happens for a multiplayer round
    (finish_round only ever sets status=completed directly for a solo
    round, which never reaches here since len(player_ids) < 2 below
    bails out first).

    Posts to *every* club every one of these players happens to share
    membership in -- not just a club the round was explicitly tagged to
    (rounds.club_id / a tournament's own club_id, the same scoping
    get_club_player_comparison uses for its stats). A group of friends
    who are all members of the same club, playing a completely untagged
    casual round at a random course, still gets a feed post there, on
    the theory that "we played together and we're all in the same club"
    is itself the interesting fact worth posting, not whether anyone
    remembered to tag the round with it. If the group shares membership
    in more than one club, it posts to all of them.

    If the players don't share membership in any club at all -- the most
    common case, e.g. two friends who golf together but belong to
    different (or no) clubs -- this is a silent no-op, not an error."""
    if len(player_ids) < 2:
        return []

    # Local import: backend.services.club_players doesn't import this
    # module, so this one's safe at module level -- kept local anyway
    # purely for symmetry with _scorecard_summary's own local import of
    # backend.services.rounds below, which *does* need to be local (that
    # one's a genuine circular-import break).
    from backend.services.club_players import list_clubs_for_player

    club_id_sets = [
        {row["club_id"] for row in list_clubs_for_player(player_id)}
        for player_id in player_ids
    ]
    shared_club_ids = set.intersection(*club_id_sets) if club_id_sets else set()
    if not shared_club_ids:
        return []

    rows = [
        {
            "club_id": club_id,
            "post_type": "scorecard",
            "author_id": None,
            "metadata": {"round_id": round_id, "player_ids": player_ids},
        }
        for club_id in shared_club_ids
    ]
    response = supabase.table("club_posts").insert(rows).execute()
    return response.data or []


def _scorecard_summary(round_id: str, player_ids: list[str]) -> dict | None:
    """Builds the small "who played, what did they shoot" summary a
    scorecard feed card needs, from the same get_round(...) every other
    round-detail view in this app already calls -- rather than
    duplicating strokes-per-hole math a third time, or freezing a copy of
    the scorecard into club_posts.metadata at post-creation time (which
    would go stale if the round were ever edited, though completed
    rounds can't be -- see RoundNotEditableError).

    Local import of backend.services.rounds is required here, not just
    stylistic -- rounds.py's own sign_off_round calls create_scorecard_
    posts (above) at module scope, so this module can't import rounds.py
    at *its* module scope too without a genuine circular import; deferring
    it to call time (well after both modules have finished loading)
    breaks that cleanly."""
    from backend.services.rounds import get_round

    round_data = get_round(round_id)
    if not round_data:
        return None

    players_by_id = {p["player_id"]: p for p in round_data.get("players", [])}
    summary_players = []
    for player_id in player_ids:
        player = players_by_id.get(player_id)
        if not player:
            continue
        total_strokes = sum(h["strokes"] for h in player["holes"] if h.get("strokes") is not None)
        thru = sum(1 for h in player["holes"] if h.get("strokes") is not None)
        name = (
            player.get("nickname")
            or f"{player.get('first_name', '')} {player.get('surname', '')}".strip()
            or "Unknown player"
        )
        summary_players.append({
            "player_id": player_id,
            "name": name,
            "total_strokes": total_strokes,
            "thru": thru,
        })

    return {
        "round_id": round_id,
        "club_name": round_data.get("club_name"),
        "course_name": round_data.get("course_name"),
        "round_date": round_data.get("round_date"),
        "players": summary_players,
    }


def get_club_feed(club_id: str, limit: int = 30) -> list[dict]:
    """Newest-first feed for one club -- every post_type mixed together
    in one list, each hydrated with whatever its own card needs to
    render standalone: an author's name/photo via the shared players
    embed below for join/tournament/manual posts, plus a scorecard
    summary fetched separately for post_type='scorecard' specifically,
    since those posts have no single author_id to embed against at all
    (author_id is null -- see create_scorecard_posts)."""
    response = (
        supabase
        .table("club_posts")
        .select(f"*, {_AUTHOR_EMBED}")
        .eq("club_id", club_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = response.data or []

    posts = []
    for row in rows:
        author = row.pop("players", None) or {}
        metadata = row.get("metadata") or {}
        post = {
            **row,
            "author_name": (
                (
                    author.get("nickname")
                    or f"{author.get('first_name', '')} {author.get('surname', '')}".strip()
                    or None
                )
                if author
                else None
            ),
            "author_photo_url": author.get("profile_picture_url") if author else None,
        }
        if row["post_type"] == "scorecard":
            post["scorecard"] = _scorecard_summary(metadata.get("round_id"), metadata.get("player_ids", []))
        posts.append(post)

    return posts