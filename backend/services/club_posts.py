# target path: backend/services/club_posts.py (new file)
import time

from backend.database import supabase
from backend.services.storage import extension_for, upload_image

CLUB_POST_PHOTO_BUCKET = "club-post-photos"

# club_posts.author_id has exactly one FK onto players (unlike
# club_invites' two), so a plain unqualified embed is unambiguous --
# same reasoning as club_players.list_players_in_club's own players(*)
# embed. Every row still in this table (join/tournament/manual) always
# has an author -- unlike the old post_type='scorecard' rows this used
# to also carry, which are sourced from round_posts now instead (see
# get_club_feed). The `if author` guard below is kept anyway as cheap
# insurance against a null author_id some other way, rather than
# assuming the DB's FK is the only thing that could ever leave it empty.
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


def get_club_feed(club_id: str, limit: int = 30) -> list[dict]:
    """Newest-first feed for one club -- every post_type mixed together
    in one list. join/tournament/manual posts come straight from
    club_posts, hydrated with the author's name/photo via the shared
    players embed below. Group-scorecard cards no longer live in
    club_posts at all -- as of the home feed feature, every completed
    round gets exactly one round_posts row (see backend/services/
    round_posts.py), and this pulls in whichever of those match this
    club's shared membership (round_posts.list_round_posts_for_club)
    rather than each club getting its own separate copy of the same
    round. post_type='scorecard' is explicitly excluded from the
    club_posts query below so a stale row from before this change (if
    you'd already run the earlier version of this feature) doesn't
    render twice alongside its round_posts-sourced replacement."""
    from backend.services.round_posts import list_round_posts_for_club

    response = (
        supabase
        .table("club_posts")
        .select(f"*, {_AUTHOR_EMBED}")
        .eq("club_id", club_id)
        .neq("post_type", "scorecard")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = response.data or []

    posts = []
    for row in rows:
        author = row.pop("players", None) or {}
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
        posts.append(post)

    posts.extend(list_round_posts_for_club(club_id))
    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return posts[:limit]