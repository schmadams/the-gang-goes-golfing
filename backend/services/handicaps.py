# target path: backend/services/handicaps.py (full replacement)
from backend.database import supabase
from backend.models.handicap import HandicapCreate
from backend.services.whs import get_player_handicap_breakdown


def add_player_handicap(handicap: HandicapCreate) -> dict:
    payload = {
        "player_id": str(handicap.player_id),
        "handicap": handicap.handicap,
        # Always "manual" -- this is the only write path a human directly
        # triggers (the My Account form). See HandicapCreate's own
        # docstring for why this isn't a client-supplied field.
        "source": "manual",
    }

    if handicap.valid_from:
        payload["valid_from"] = handicap.valid_from.isoformat()

    response = (
        supabase
        .table("player_handicaps")
        .insert(payload)
        .execute()
    )

    return response.data[0]


def list_player_handicaps(player_id: str) -> list[dict]:
    response = (
        supabase
        .table("player_handicaps")
        .select("*")
        .eq("player_id", player_id)
        .order("valid_from", desc=True)
        .execute()
    )

    return response.data


def get_current_player_handicap(player_id: str, source: str | None = None) -> dict | None:
    """Latest handicap on file for this player -- optionally scoped to
    one source ('t3g' or 'manual'). Without a source, this is the latest
    row of EITHER kind, which is the app's original (and still frequently
    wrong-feeling) behaviour: a manual entry and a WHS recalculation can
    freely supersede each other with no way to tell which one "your
    handicap" actually means at a glance. Every call site that determines
    what a specific round or tournament entry should actually use now
    passes an explicit source (see get_effective_handicap_source below) --
    this bare default is kept only for places that still want "whatever's
    most recent, regardless of kind" (e.g. the club directory's handicap
    column, a lightweight glance rather than something with scoring
    consequences)."""
    query = (
        supabase
        .table("player_handicaps")
        .select("*")
        .eq("player_id", player_id)
    )
    if source is not None:
        query = query.eq("source", source)

    response = query.order("valid_from", desc=True).limit(1).execute()

    if not response.data:
        return None

    return response.data[0]


def get_effective_handicap_source(player_id: str, override: str | None = None) -> str:
    """Resolves which handicap source ('t3g' or 'manual') should actually
    be used for a specific round or tournament entry: an explicit
    override (a per-round/per-entry choice someone made) wins if given,
    otherwise falls back to the player's own account-level
    preferred_handicap_source. Centralised here rather than duplicated at
    every call site that needs this decision."""
    if override in ("t3g", "manual"):
        return override

    from backend.services.players import get_player

    player = get_player(player_id)
    preferred = (player or {}).get("preferred_handicap_source")
    return preferred if preferred in ("t3g", "manual") else "t3g"


def get_player_handicap_sources(player_id: str) -> dict:
    """Both current handicaps side by side -- the T3G (WHS-calculated)
    one and the manually-entered one -- plus which one this player has
    set as their account default. Powers My Account's split display,
    which is the whole reason a player can tell the two apart at all
    instead of just seeing whichever happens to be newest."""
    t3g_row = get_current_player_handicap(player_id, source="t3g")
    manual_row = get_current_player_handicap(player_id, source="manual")
    return {
        "t3g": t3g_row,
        "manual": manual_row,
        "preferred_source": get_effective_handicap_source(player_id),
    }


def list_latest_handicaps_for_club(club_id: str) -> list[dict]:
    club_players_response = (
        supabase
        .table("club_players")
        .select("club_id, player_id, players(id, first_name, surname, date_of_birth)")
        .eq("club_id", club_id)
        .execute()
    )

    club_player_rows = club_players_response.data

    results = []

    for row in club_player_rows:
        player = row.get("players") or {}
        player_id = row["player_id"]

        handicap_response = (
            supabase
            .table("player_handicaps")
            .select("id, player_id, handicap, valid_from, created_at")
            .eq("player_id", player_id)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )

        latest_handicap = handicap_response.data[0] if handicap_response.data else None

        results.append(
            {
                "club_id": row["club_id"],
                "player_id": player_id,
                "first_name": player.get("first_name"),
                "surname": player.get("surname"),
                "date_of_birth": player.get("date_of_birth"),
                "latest_handicap": latest_handicap,
            }
        )

    return results


def get_handicap_breakdown(player_id: str) -> dict:
    """Thin pass-through to the WHS engine -- kept here (rather than
    having routers/handicaps.py import backend.services.whs directly) so
    every other handicap read in the app goes through this one service
    module, consistent with the rest of this file."""
    return get_player_handicap_breakdown(player_id)