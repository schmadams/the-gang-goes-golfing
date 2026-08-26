# target path: backend/services/club_players.py (replaces backend/services/group_players.py, which should be deleted)
from backend.database import supabase
from backend.models.club_player import ClubPlayerCreate, ClubPlayerDelete


def add_player_to_club(club_player: ClubPlayerCreate) -> dict:
    payload = {
        "club_id": str(club_player.club_id),
        "player_id": str(club_player.player_id),
    }

    response = (
        supabase
        .table("club_players")
        .insert(payload)
        .execute()
    )
    row = response.data[0]

    # A club with no admin can never gain new members through the normal
    # invite flow (only the admin can send invites -- see
    # club_invites.send_club_invite), so it'd be permanently stuck. If this
    # club doesn't have one yet, whoever just joined becomes it -- covers
    # both "the creator becomes admin" (create_club's auto-join call) and
    # legacy/seed data that predates every path setting one (e.g. a club
    # created with a single member and no admin assigned, like Spam vs
    # Chiggim).
    club_response = (
        supabase
        .table("clubs")
        .select("club_admin")
        .eq("id", payload["club_id"])
        .maybe_single()
        .execute()
    )
    club = club_response.data if club_response is not None else None
    if club and club.get("club_admin") is None:
        supabase.table("clubs").update({"club_admin": payload["player_id"]}).eq("id", payload["club_id"]).execute()

    # Local import to avoid a circular import -- club_posts.create_
    # scorecard_posts itself imports list_clubs_for_player from this
    # same module, so this module can't import club_posts at module
    # scope too. Best-effort: a feed post failing should never be able
    # to block someone actually joining a club.
    try:
        from backend.services.club_posts import create_join_post
        create_join_post(payload["club_id"], payload["player_id"])
    except Exception as exc:
        print(f"[FEED] Failed to create join post for club={payload['club_id']} player={payload['player_id']}: {exc}")

    return row


def list_players_in_club(club_id: str) -> list[dict]:
    response = (
        supabase
        .table("club_players")
        .select("club_id, player_id, created_at, players(*)")
        .eq("club_id", club_id)
        .order("created_at")
        .execute()
    )

    return response.data


def list_clubs_for_player(player_id: str) -> list[dict]:
    response = (
        supabase
        .table("club_players")
        .select("club_id, player_id, created_at, clubs(*)")
        .eq("player_id", player_id)
        .order("created_at")
        .execute()
    )

    return response.data


def remove_player_from_club(club_player: ClubPlayerDelete) -> dict | None:
    response = (
        supabase
        .table("club_players")
        .delete()
        .eq("club_id", str(club_player.club_id))
        .eq("player_id", str(club_player.player_id))
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]