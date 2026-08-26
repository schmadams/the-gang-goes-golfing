# target path: backend/services/tournaments.py (full replacement)
from backend.database import supabase
from backend.models.tournament import (
    VALID_ENTRY_MODES,
    VALID_GROUPING_METHODS,
    VALID_TOURNAMENT_FORMATS,
    TournamentCreate,
    TournamentUpdate,
)
from backend.services.handicaps import get_current_player_handicap
from backend.services.rounds import _hole_handicap_strokes, _stableford_points
from backend.services.tournament_tee_times import fetch_tee_times_by_round

_PLAYER_EMBED = "players(id, first_name, surname, nickname, profile_picture_url)"


class ClubNotFoundError(Exception):
    """Raised when club_id doesn't match any club."""


class NotClubAdminError(Exception):
    """Raised if someone other than the club's admin tries to create a
    tournament -- same restriction as club_invites.send_club_invite."""


class InvalidFormatError(Exception):
    """Raised when format isn't one of VALID_TOURNAMENT_FORMATS."""


class InvalidEntryModeError(Exception):
    """Raised when entry_mode isn't one of VALID_ENTRY_MODES."""


class InvalidGroupingMethodError(Exception):
    """Raised when grouping_method isn't one of VALID_GROUPING_METHODS."""


class NoRoundsError(Exception):
    """Raised when a tournament is submitted with zero rounds -- there's
    nothing to play otherwise."""


class TournamentNotFoundError(Exception):
    """Raised when tournament_id doesn't match any tournament."""


class TournamentRoundNotFoundError(Exception):
    """Raised when a leaderboard is requested for a round_id that doesn't
    belong to the given tournament (or doesn't exist at all)."""


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

    tee_times_by_round = fetch_tee_times_by_round([r["id"] for r in enriched])
    for r in enriched:
        r["tee_times"] = tee_times_by_round.get(r["id"], [])

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
            "photo_url": player.get("profile_picture_url"),
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
    if payload.grouping_method not in VALID_GROUPING_METHODS:
        raise InvalidGroupingMethodError(
            f"Grouping method must be one of: {', '.join(sorted(VALID_GROUPING_METHODS))}."
        )
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
            "grouping_method": payload.grouping_method,
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
            "group_size": r.group_size,
        }
        for index, r in enumerate(payload.rounds)
    ]
    rounds_response = supabase.table("tournament_rounds").insert(round_rows).execute()
    rounds = _attach_course_names(rounds_response.data or [])
    for r in rounds:
        r["tee_times"] = []

    return {**tournament, "rounds": rounds, "entrants": []}


def update_tournament(tournament_id: str, payload: TournamentUpdate) -> dict:
    existing_response = supabase.table("tournaments").select("*").eq("id", tournament_id).maybe_single().execute()
    tournament = existing_response.data if existing_response is not None else None
    if not tournament:
        raise TournamentNotFoundError("Tournament not found.")

    club = _get_club(tournament["club_id"])
    if not club or str(club.get("club_admin")) != str(payload.admin_id):
        raise NotClubAdminError("Only this club's admin can edit tournaments.")
    if payload.format not in VALID_TOURNAMENT_FORMATS:
        raise InvalidFormatError(f"Format must be one of: {', '.join(sorted(VALID_TOURNAMENT_FORMATS))}.")
    if payload.entry_mode not in VALID_ENTRY_MODES:
        raise InvalidEntryModeError(f"Entry mode must be one of: {', '.join(sorted(VALID_ENTRY_MODES))}.")
    if payload.grouping_method not in VALID_GROUPING_METHODS:
        raise InvalidGroupingMethodError(
            f"Grouping method must be one of: {', '.join(sorted(VALID_GROUPING_METHODS))}."
        )
    if not payload.rounds:
        raise NoRoundsError("A tournament needs at least one round.")

    tournament_response = (
        supabase
        .table("tournaments")
        .update({
            "name": payload.name,
            "format": payload.format,
            "entry_mode": payload.entry_mode,
            "min_handicap": payload.min_handicap,
            "max_handicap": payload.max_handicap,
            "grouping_method": payload.grouping_method,
        })
        .eq("id", tournament_id)
        .execute()
    )
    updated_tournament = tournament_response.data[0]

    # Rounds are replaced wholesale rather than diffed against the existing
    # rows -- the edit form resubmits its entire round list every time
    # (same shape create_tournament accepts), so delete-then-reinsert keeps
    # round_number/order in sync with whatever the form now says without
    # matching old rows to new ones by id. This also means any tee times
    # generated against the old rounds get cascade-deleted along with them
    # (tournament_tee_times FKs to tournament_rounds.id) -- an edit that
    # changes a round's date/course effectively invalidates its tee times
    # anyway, so the admin re-running Generate afterward is the right
    # prompt, not something to silently preserve.
    supabase.table("tournament_rounds").delete().eq("tournament_id", tournament_id).execute()

    round_rows = [
        {
            "tournament_id": tournament_id,
            "round_number": index + 1,
            "round_date": r.round_date.isoformat(),
            "course_id": str(r.course_id),
            "tee_id": str(r.tee_id),
            "group_size": r.group_size,
        }
        for index, r in enumerate(payload.rounds)
    ]
    rounds_response = supabase.table("tournament_rounds").insert(round_rows).execute()
    rounds = _attach_course_names(rounds_response.data or [])
    for r in rounds:
        r["tee_times"] = []

    entrants_by_tournament = _fetch_entrants_by_tournament([tournament_id])

    return {
        **updated_tournament,
        "rounds": rounds,
        "entrants": entrants_by_tournament.get(tournament_id, []),
    }


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


def _course_holes_meta(tee_id: str) -> dict[int, dict]:
    """{hole_number: {"par":, "stroke_index":}} for one tee -- what every
    per-player leaderboard line for a round is measured against."""
    response = (
        supabase
        .table("course_holes")
        .select("hole_number, par, stroke_index")
        .eq("tee_id", tee_id)
        .order("hole_number")
        .execute()
    )
    return {h["hole_number"]: h for h in (response.data or [])}


def _tournament_round_scores_by_player(
    tournament_round_id: str,
) -> tuple[dict[str, dict[int, int]], dict[str, dict[int, bool]]]:
    """Every accepted player's per-hole strokes across *every* grouping's
    live/finished round for one tournament round -- not just one tee time,
    since the leaderboard covers the whole field, not one viewer's own
    grouping the way the Start Sheet/Live Round tabs do.

    Returns (scores_by_player, nr_by_player). scores_by_player is
    {player_id: {hole_number: strokes}}, holes with no score yet -- or
    marked No Return, see below -- simply absent, same as before this
    function also tracked NR. nr_by_player is the parallel {player_id:
    {hole_number: True}} map of which specific holes were marked NR
    (mark_round_no_result in backend/services/rounds.py, or the live
    scorecard's per-hole "NR" save action) -- used both to decide whether
    a player's round-level total counts as NR at all (get_tournament_
    leaderboard) and to show which hole it happened on in the per-player
    scorecard (see _compute_leaderboard_line's holes_nr output)."""
    rounds_response = (
        supabase
        .table("rounds")
        .select("id")
        .eq("tournament_round_id", tournament_round_id)
        .execute()
    )
    round_ids = [r["id"] for r in (rounds_response.data or [])]
    if not round_ids:
        return {}, {}

    scores_response = (
        supabase
        .table("round_scores")
        .select("player_id, hole_number, strokes, nr")
        .in_("round_id", round_ids)
        .execute()
    )
    by_player: dict[str, dict[int, int]] = {}
    nr_by_player: dict[str, dict[int, bool]] = {}
    for row in (scores_response.data or []):
        if row.get("nr"):
            nr_by_player.setdefault(row["player_id"], {})[row["hole_number"]] = True
            continue
        if row.get("strokes") is None:
            continue
        by_player.setdefault(row["player_id"], {})[row["hole_number"]] = row["strokes"]
    return by_player, nr_by_player


def _compute_leaderboard_line(
    scores_by_hole: dict[int, int],
    holes_meta: dict[int, dict],
    handicap: float | None,
    nr_by_hole: dict[int, bool] | None = None,
) -> dict:
    """Cumulative gross/nett-to-par and cumulative Stableford points through
    each of the 18 holes for one player in one round -- None for any hole
    they haven't played yet (or that has no par on record), so the
    frontend can render a blank cell instead of a fabricated running
    total. gross/nett "to par" is relative to the *par of the holes
    actually played so far*, not all 18, same convention every real
    leaderboard uses for an in-progress round -- see _hole_handicap_
    strokes/_stableford_points in backend/services/rounds.py for the
    handicap-stroke-allocation and points math this reuses.

    nr_by_hole ({hole_number: True} for whichever holes this player
    marked No Return, from _tournament_round_scores_by_player) doesn't
    change any of the cumulative math above -- an NR'd hole never has a
    strokes value, so it's already skipped exactly like an unplayed hole.
    It only drives two extra outputs: holes_nr (the same shape as holes_
    strokes, for the per-player scorecard modal to show "NR" specifically
    rather than a blank on that hole) and is_nr (True the moment *any*
    hole in this round was marked NR) -- get_tournament_leaderboard reads
    is_nr to decide whether this player's whole round (and, once it's
    happened in any round up to and including the one being viewed, their
    tournament-to-date total too) sorts to the bottom of the leaderboard
    with "NR" in place of a score, instead of ranking them normally."""
    nr_by_hole = nr_by_hole or {}
    holes_gross: list[int | None] = []
    holes_nett: list[int | None] = []
    holes_stableford: list[int | None] = []
    # Raw strokes per hole, alongside the cumulative-to-par lines above --
    # not used by the leaderboard grid itself (that's all cumulative), but
    # it's what the click-through per-player scorecard (see tournament.py's
    # _leaderboard_player_scorecard) renders, so clicking a row doesn't
    # need a second round trip to reconstruct it from the deltas.
    holes_strokes: list[int | None] = []
    holes_nr: list[bool] = []

    running_par = 0
    running_gross = 0
    running_nett = 0
    running_points = 0
    thru = 0

    for hole_number in range(1, 19):
        strokes = scores_by_hole.get(hole_number)
        hole = holes_meta.get(hole_number)
        par = hole.get("par") if hole else None
        holes_nr.append(bool(nr_by_hole.get(hole_number)))

        if strokes is None or par is None:
            holes_gross.append(None)
            holes_nett.append(None)
            holes_stableford.append(None)
            holes_strokes.append(None)
            continue

        thru += 1
        stroke_index = hole.get("stroke_index")
        hcp_strokes = _hole_handicap_strokes(handicap, stroke_index)
        net_strokes = strokes - hcp_strokes

        running_par += par
        running_gross += strokes
        running_nett += net_strokes
        running_points += _stableford_points(net_strokes, par) or 0

        holes_gross.append(running_gross - running_par)
        holes_nett.append(running_nett - running_par)
        holes_stableford.append(running_points)
        holes_strokes.append(strokes)

    return {
        "holes_gross": holes_gross,
        "holes_nett": holes_nett,
        "holes_stableford": holes_stableford,
        "holes_strokes": holes_strokes,
        "holes_nr": holes_nr,
        "is_nr": any(holes_nr),
        "total_gross": holes_gross[thru - 1] if thru else None,
        "total_nett": holes_nett[thru - 1] if thru else None,
        "total_stableford": holes_stableford[thru - 1] if thru else 0,
        "thru": thru,
    }


def get_tournament_leaderboard(tournament_id: str, round_id: str) -> dict:
    """Live, whole-field leaderboard for one round of a tournament --
    every confirmed entrant, sorted leader-first once the frontend applies
    whichever of gross/stableford/nett it's currently displaying (all
    three are computed here regardless, so switching format on the
    frontend is instant with no extra request). PRIOR is each player's
    cumulative total from every *earlier* round of this same tournament
    (0 if they never played, or weren't grouped into, an earlier round --
    a missed round isn't retroactively penalized beyond what they actually
    shot); the round grid itself is only this one selected round's holes.
    """
    round_response = (
        supabase.table("tournament_rounds").select("*").eq("id", round_id).maybe_single().execute()
    )
    tournament_round = round_response.data if round_response is not None else None
    if not tournament_round or tournament_round["tournament_id"] != tournament_id:
        raise TournamentRoundNotFoundError("Round not found for this tournament.")

    entrants_by_tournament = _fetch_entrants_by_tournament([tournament_id])
    entrants = [e for e in entrants_by_tournament.get(tournament_id, []) if e["status"] == "confirmed"]

    all_rounds_response = (
        supabase
        .table("tournament_rounds")
        .select("id, round_number, tee_id")
        .eq("tournament_id", tournament_id)
        .order("round_number")
        .execute()
    )
    all_rounds = all_rounds_response.data or []
    earlier_rounds = [r for r in all_rounds if r["round_number"] < tournament_round["round_number"]]

    handicap_by_player: dict[str, float | None] = {}
    for entrant in entrants:
        handicap_row = get_current_player_handicap(entrant["player_id"])
        handicap_by_player[entrant["player_id"]] = handicap_row["handicap"] if handicap_row else None

    # Prior totals -- sum each earlier round's own line (its own course/
    # tee, so its own holes_meta and its own scores) per format, per
    # player. A player who never played a given earlier round contributes
    # 0 to it, not a penalty. prior_is_nr tracks whether a player was
    # marked No Return in *any* earlier round -- once that's happened,
    # their tournament-to-date total can't mean anything from that point
    # forward either (same convention every real competition uses -- an
    # NR round breaks the cumulative card, not just that one round's own
    # line), so it carries forward through every later round's view of
    # the leaderboard regardless of how they scored afterward.
    prior_gross = {e["player_id"]: 0 for e in entrants}
    prior_nett = {e["player_id"]: 0 for e in entrants}
    prior_stableford = {e["player_id"]: 0 for e in entrants}
    prior_is_nr = {e["player_id"]: False for e in entrants}

    for earlier in earlier_rounds:
        holes_meta = _course_holes_meta(earlier["tee_id"])
        scores_by_player, nr_by_player = _tournament_round_scores_by_player(earlier["id"])
        for entrant in entrants:
            player_id = entrant["player_id"]
            line = _compute_leaderboard_line(
                scores_by_player.get(player_id, {}),
                holes_meta,
                handicap_by_player[player_id],
                nr_by_player.get(player_id, {}),
            )
            prior_gross[player_id] += line["total_gross"] or 0
            prior_nett[player_id] += line["total_nett"] or 0
            prior_stableford[player_id] += line["total_stableford"]
            if line["is_nr"]:
                prior_is_nr[player_id] = True

    holes_meta = _course_holes_meta(tournament_round["tee_id"])
    scores_by_player, nr_by_player = _tournament_round_scores_by_player(round_id)

    players = []
    for entrant in entrants:
        player_id = entrant["player_id"]
        line = _compute_leaderboard_line(
            scores_by_player.get(player_id, {}),
            holes_meta,
            handicap_by_player[player_id],
            nr_by_player.get(player_id, {}),
        )
        name = entrant.get("nickname") or f"{entrant.get('first_name', '')} {entrant.get('surname', '')}".strip()

        players.append({
            "player_id": player_id,
            "name": name or "Unknown player",
            # Carried straight through from _fetch_entrants_by_tournament's
            # own player embed -- lets every leaderboard avatar show the
            # real profile picture once one's been uploaded (see
            # _leaderboard_avatar in tournament.py), falling back to
            # initials there when this is None the same as it always did.
            "photo_url": entrant.get("photo_url"),
            "thru": line["thru"],
            "holes_gross": line["holes_gross"],
            "holes_nett": line["holes_nett"],
            "holes_stableford": line["holes_stableford"],
            "holes_strokes": line["holes_strokes"],
            "holes_nr": line["holes_nr"],
            # True the moment this player is NR in this round or any
            # earlier one feeding this same cumulative view -- see the
            # prior_is_nr comment above. Sorting/display (bottom of the
            # board, "NR" instead of a number) is entirely the frontend's
            # call based on this one flag; the numeric totals below are
            # still computed and included regardless, in case they're
            # ever useful, but shouldn't be shown as a real rank once
            # this is true.
            "is_nr": prior_is_nr[player_id] or line["is_nr"],
            "prior_gross": prior_gross[player_id],
            "prior_nett": prior_nett[player_id],
            "prior_stableford": prior_stableford[player_id],
            "total_gross": prior_gross[player_id] + (line["total_gross"] or 0),
            "total_nett": prior_nett[player_id] + (line["total_nett"] or 0),
            "total_stableford": prior_stableford[player_id] + line["total_stableford"],
        })

    return {
        "round_id": round_id,
        "round_number": tournament_round["round_number"],
        "holes": [
            {"hole_number": n, "par": holes_meta.get(n, {}).get("par")}
            for n in range(1, 19)
        ],
        "players": players,
    }