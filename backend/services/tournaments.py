# target path: backend/services/tournaments.py (full replacement)
from backend.database import supabase
from backend.models.tournament import VALID_ENTRY_MODES, VALID_TOURNAMENT_FORMATS, TournamentCreate

_PLAYER_EMBED = "players(id, first_name, surname, nickname)"


class ClubNotFoundError(Exception):
    """Raised when club_id doesn't match any club."""


class NotClubAdminError(Exception):
    """Raised if someone other than the club's admin tries to create a
    tournament -- same restriction as club_invites.send_club_invite."""


class InvalidFormatError(Exception):
    """Raised when format isn't one of VALID_TOURNAMENT_FORMATS."""


class InvalidEntryModeError(Exception):
    """Raised when entry_mode isn't one of VALID_ENTRY_MODES."""


class NoRoundsError(Exception):
    """Raised when a tournament is submitted with zero rounds -- there's
    nothing to play otherwise."""


class TournamentNotFoundError(Exception):
    """Raised when tournament_id doesn't match any tournament."""


def _get_club(club_id: str) -> dict | None:
    response = supabase.table("clubs").select("*").eq("id", club_id).maybe_single().execute()
    return response.data if response is not None else None


def _attach_course_names(rounds: list[dict]) -> list[dict]:
    """Enriches a batch of tournament_rounds rows with the course/tee
    display names the frontend needs -- one query per table across the
    whole batch rather than per-row, same reasoning as courses.get_course's
    N+1 warning, just avoided here instead of accepted."""
    if not rounds:
        return []

    course_ids = list({r["course_id"] for r in rounds})
    courses_response = (
        supabase.table("courses").select("id, club_name, course_name").in_("id", course_ids).execute()
    )
    courses_by_id = {c["id"]: c for c in (courses_response.data or [])}

    tee_ids = list({r["tee_id"] for r in rounds})
    tees_response = supabase.table("course_tees").select("id, name").in_("id", tee_ids).execute()
    tees_by_id = {t["id"]: t for t in (tees_response.data or [])}

    enriched = []
    for r in rounds:
        course = courses_by_id.get(r["course_id"], {})
        tee = tees_by_id.get(r["tee_id"], {})
        enriched.append({
            **r,
            "club_name": course.get("club_name"),
            "course_name": course.get("course_name"),
            "tee_name": tee.get("name"),
        })
    return enriched


def _fetch_rounds_by_tournament(tournament_ids: list[str]) -> dict[str, list[dict]]:
    if not tournament_ids:
        return {}

    rounds_response = (
        supabase
        .table("tournament_rounds")
        .select("*")
        .in_("tournament_id", tournament_ids)
        .order("round_number")
        .execute()
    )
    enriched = _attach_course_names(rounds_response.data or [])

    grouped: dict[str, list[dict]] = {}
    for r in enriched:
        grouped.setdefault(r["tournament_id"], []).append(r)
    return grouped


def _fetch_entrants_by_tournament(tournament_ids: list[str]) -> dict[str, list[dict]]:
    """Same batching approach as _fetch_rounds_by_tournament -- one query
    for every tournament in the batch instead of one per tournament, with
    player display info embedded directly (name shown wherever entrants
    are listed, so it's always needed alongside the row)."""
    if not tournament_ids:
        return {}

    entrants_response = (
        supabase
        .table("tournament_entrants")
        .select(f"*, {_PLAYER_EMBED}")
        .in_("tournament_id", tournament_ids)
        .order("created_at")
        .execute()
    )

    grouped: dict[str, list[dict]] = {}
    for entrant in (entrants_response.data or []):
        player = entrant.pop("players", None) or {}
        flat = {
            **entrant,
            "first_name": player.get("first_name"),
            "surname": player.get("surname"),
            "nickname": player.get("nickname"),
        }
        grouped.setdefault(flat["tournament_id"], []).append(flat)
    return grouped


def create_tournament(payload: TournamentCreate) -> dict:
    club = _get_club(str(payload.club_id))
    if not club:
        raise ClubNotFoundError("Club not found.")
    if str(club.get("club_admin")) != str(payload.admin_id):
        raise NotClubAdminError("Only this club's admin can create tournaments.")
    if payload.format not in VALID_TOURNAMENT_FORMATS:
        raise InvalidFormatError(f"Format must be one of: {', '.join(sorted(VALID_TOURNAMENT_FORMATS))}.")
    if payload.entry_mode not in VALID_ENTRY_MODES:
        raise InvalidEntryModeError(f"Entry mode must be one of: {', '.join(sorted(VALID_ENTRY_MODES))}.")
    if not payload.rounds:
        raise NoRoundsError("A tournament needs at least one round.")

    tournament_response = (
        supabase
        .table("tournaments")
        .insert({
            "club_id": str(payload.club_id),
            "name": payload.name,
            "format": payload.format,
            "created_by": str(payload.admin_id),
            "entry_mode": payload.entry_mode,
            "min_handicap": payload.min_handicap,
            "max_handicap": payload.max_handicap,
        })
        .execute()
    )
    tournament = tournament_response.data[0]

    round_rows = [
        {
            "tournament_id": tournament["id"],
            "round_number": index + 1,
            "round_date": r.round_date.isoformat(),
            "course_id": str(r.course_id),
            "tee_id": str(r.tee_id),
        }
        for index, r in enumerate(payload.rounds)
    ]
    rounds_response = supabase.table("tournament_rounds").insert(round_rows).execute()

    return {**tournament, "rounds": _attach_course_names(rounds_response.data or []), "entrants": []}


def list_tournaments_for_club(club_id: str) -> list[dict]:
    tournaments_response = (
        supabase
        .table("tournaments")
        .select("*")
        .eq("club_id", club_id)
        .order("created_at", desc=True)
        .execute()
    )
    tournaments = tournaments_response.data or []
    if not tournaments:
        return []

    tournament_ids = [t["id"] for t in tournaments]
    rounds_by_tournament = _fetch_rounds_by_tournament(tournament_ids)
    entrants_by_tournament = _fetch_entrants_by_tournament(tournament_ids)

    return [
        {
            **t,
            "rounds": rounds_by_tournament.get(t["id"], []),
            "entrants": entrants_by_tournament.get(t["id"], []),
        }
        for t in tournaments
    ]


def get_tournament(tournament_id: str) -> dict | None:
    response = supabase.table("tournaments").select("*").eq("id", tournament_id).maybe_single().execute()
    tournament = response.data if response is not None else None
    if not tournament:
        return None

    rounds_by_tournament = _fetch_rounds_by_tournament([tournament_id])
    entrants_by_tournament = _fetch_entrants_by_tournament([tournament_id])

    return {
        **tournament,
        "rounds": rounds_by_tournament.get(tournament_id, []),
        "entrants": entrants_by_tournament.get(tournament_id, []),
    }