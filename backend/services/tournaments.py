# target path: backend/services/tournaments.py (full replacement)
from datetime import datetime, timezone

from backend.database import supabase
from backend.models.tournament import (
    VALID_ENTRY_MODES,
    VALID_GROUPING_METHODS,
    VALID_HANDICAP_ALLOWANCES,
    VALID_PAIRS_SCORING_STYLES,
    VALID_TOURNAMENT_FORMATS,
    TournamentCreate,
    TournamentFinalizeRequest,
    TournamentUpdate,
)
from backend.services.handicaps import get_current_player_handicap, get_effective_handicap_source
from backend.services.rounds import _hole_handicap_strokes, _stableford_points
from backend.services.tournament_pairs import list_tournament_pairs
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


class InvalidHandicapAllowanceError(Exception):
    """Raised when handicap_allowance isn't one of VALID_HANDICAP_ALLOWANCES."""


class InvalidPairsScoringStyleError(Exception):
    """Raised when pairs_scoring_style isn't one of VALID_PAIRS_SCORING_STYLES."""


class NoRoundsError(Exception):
    """Raised when a tournament is submitted with zero rounds -- there's
    nothing to play otherwise."""


class TournamentNotFoundError(Exception):
    """Raised when tournament_id doesn't match any tournament."""


class TournamentRoundNotFoundError(Exception):
    """Raised when a leaderboard is requested for a round_id that doesn't
    belong to the given tournament (or doesn't exist at all)."""


class InvalidLinkError(Exception):
    """Raised when a linked_tournament_id fails one of the sharing rules
    below -- see _validate_link."""


class TournamentNotReadyToFinalizeError(Exception):
    """Raised when finalize_tournament is called before every round has
    been played and fully signed off -- see
    _all_tournament_rounds_completed. Its message is the specific
    admin-facing reason (which round, and why) rather than a generic
    "not ready" string."""


class TournamentAlreadyFinalizedError(Exception):
    """Raised when finalize_tournament is called on a tournament whose
    finalized_at is already set -- finalizing is a one-way action (there's
    no un-finalize), so a second attempt is almost always a stale page
    double-submitting the same click rather than a real request to
    recompute the standings."""


def _get_club(club_id: str) -> dict | None:
    response = supabase.table("clubs").select("*").eq("id", club_id).maybe_single().execute()
    return response.data if response is not None else None


def _validate_link(club_id: str, own_id: str | None, linked_tournament_id: str | None) -> None:
    """Enforces the rules a linked_tournament_id has to satisfy, shared by
    create_tournament (own_id=None, nothing to conflict with yet) and
    update_tournament (own_id=this tournament's real id, so it can be
    told apart from its own current link when checking for conflicts).
    A no-op when linked_tournament_id is None -- unlinking, or never
    having been linked, needs no validation."""
    if linked_tournament_id is None:
        return
    if own_id is not None and str(linked_tournament_id) == str(own_id):
        raise InvalidLinkError("A tournament can't be linked to itself.")

    target_response = (
        supabase
        .table("tournaments")
        .select("id, club_id, linked_tournament_id")
        .eq("id", str(linked_tournament_id))
        .maybe_single()
        .execute()
    )
    target = target_response.data if target_response is not None else None
    if not target:
        raise TournamentNotFoundError("The tournament you're trying to link to wasn't found.")
    if str(target["club_id"]) != str(club_id):
        raise InvalidLinkError("You can only link tournaments within the same club.")
    # A shadow of a shadow would make "which tournament actually owns the
    # data" ambiguous -- link to the source itself instead.
    if target.get("linked_tournament_id"):
        raise InvalidLinkError(
            "That tournament is itself linked to another one -- link to its source tournament instead."
        )

    # 1:1 -- a source tournament can only have one shadow. own_id is
    # excluded from the conflict check so re-saving an unchanged existing
    # link doesn't trip over itself.
    shadow_response = (
        supabase.table("tournaments").select("id").eq("linked_tournament_id", str(linked_tournament_id)).execute()
    )
    existing_shadow_ids = {row["id"] for row in (shadow_response.data or [])}
    if own_id is not None:
        existing_shadow_ids.discard(str(own_id))
    if existing_shadow_ids:
        raise InvalidLinkError("That tournament is already linked to another tournament.")


def _resolve_source_tournament_id(tournament_id: str) -> str:
    """A tournament linked to another (tournaments.linked_tournament_id)
    is a *shadow* -- it owns no entrants, rounds, or tee times of its
    own; those all live under the tournament it's linked to (the
    *source*). Every place below that reads or writes entrants/rounds/
    tee-times for a tournament resolves through this first, so a
    shadow's Entrants/Start Sheet/Live Round tabs transparently show
    (and edit) the exact same underlying rows as its linked tournament --
    one shared roster and tee sheet, not two kept in sync by hand. Falls
    back to the given id unchanged when the tournament isn't linked, or
    isn't found at all (an invalid id downstream is the caller's own
    not-found handling to catch, not this helper's)."""
    response = (
        supabase.table("tournaments").select("linked_tournament_id").eq("id", tournament_id).maybe_single().execute()
    )
    row = response.data if response is not None else None
    return (row or {}).get("linked_tournament_id") or tournament_id


def _attach_link_info(tournaments: list[dict]) -> list[dict]:
    """Enriches each tournament row with its own outgoing link name (if
    it's a shadow) and the reverse -- some other tournament that's a
    shadow of *this* one, if any -- so both sides of a linked pair can
    show/navigate to each other without a separate fetch. Same batched-
    query shape as _attach_course_names above.

    Also overlays a shadow's entry_mode/min_handicap/max_handicap/
    grouping_method with its linked tournament's own values -- these are
    already what's functionally enforced for a shadow (see tournament_
    entrants.py's _resolve_shared_roster, which validates entry against
    the *linked* tournament's row, not this one), so this keeps every
    displayed copy (Tournament Info tab, Edit modal pre-fill) in sync
    with that instead of showing a shadow's own inert settings. format
    and handicap_allowance are deliberately left out of this overlay --
    those stay each tournament's own choice even when linked (e.g. an
    individual Stableford source paired with a Pairs Better Ball shadow
    scored at a different allowance). Reuses the same query that already
    fetches the linked tournament's name, so this costs nothing extra."""
    if not tournaments:
        return []

    linked_ids = list({t["linked_tournament_id"] for t in tournaments if t.get("linked_tournament_id")})
    linked_by_id: dict[str, dict] = {}
    if linked_ids:
        linked_response = (
            supabase
            .table("tournaments")
            .select("id, name, entry_mode, min_handicap, max_handicap, grouping_method")
            .in_("id", linked_ids)
            .execute()
        )
        linked_by_id = {t["id"]: t for t in (linked_response.data or [])}

    ids = [t["id"] for t in tournaments]
    shadow_response = (
        supabase.table("tournaments").select("id, name, linked_tournament_id").in_("linked_tournament_id", ids).execute()
    )
    shadow_by_source: dict[str, dict] = {}
    for shadow in (shadow_response.data or []):
        shadow_by_source[shadow["linked_tournament_id"]] = shadow

    enriched = []
    for t in tournaments:
        linked_id = t.get("linked_tournament_id")
        source = linked_by_id.get(linked_id) if linked_id else None
        shadow = shadow_by_source.get(t["id"])
        merged = {
            **t,
            "linked_tournament_name": source["name"] if source else None,
            "linked_from_tournament_id": shadow["id"] if shadow else None,
            "linked_from_tournament_name": shadow["name"] if shadow else None,
        }
        if source:
            merged["entry_mode"] = source["entry_mode"]
            merged["min_handicap"] = source["min_handicap"]
            merged["max_handicap"] = source["max_handicap"]
            merged["grouping_method"] = source["grouping_method"]
        enriched.append(merged)
    return enriched


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
    if payload.handicap_allowance not in VALID_HANDICAP_ALLOWANCES:
        raise InvalidHandicapAllowanceError(
            f"Handicap allowance must be one of: {', '.join(str(v) for v in sorted(VALID_HANDICAP_ALLOWANCES))}."
        )
    if payload.pairs_scoring_style not in VALID_PAIRS_SCORING_STYLES:
        raise InvalidPairsScoringStyleError(
            f"Pairs scoring style must be one of: {', '.join(sorted(VALID_PAIRS_SCORING_STYLES))}."
        )
    linked_tournament_id = str(payload.linked_tournament_id) if payload.linked_tournament_id else None
    _validate_link(str(payload.club_id), None, linked_tournament_id)
    # A shadow tournament (linked_tournament_id set) plays its linked
    # tournament's rounds -- it's not allowed to have none at all, but
    # only because it isn't asked to submit any of its own; a normal,
    # unlinked tournament still needs at least one.
    if not linked_tournament_id and not payload.rounds:
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
            "handicap_allowance": payload.handicap_allowance,
            "pairs_scoring_style": payload.pairs_scoring_style,
            "linked_tournament_id": linked_tournament_id,
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
    if round_rows:
        rounds_response = supabase.table("tournament_rounds").insert(round_rows).execute()
        rounds = _attach_course_names(rounds_response.data or [])
        for r in rounds:
            r["tee_times"] = []
    else:
        rounds = []

    # Best-effort -- a feed post failing should never be able to block
    # the tournament itself from being created. Local import for the
    # same "avoid a module-scope circular import" reason as club_players.
    # add_player_to_club's own join-post hook.
    try:
        from backend.services.club_posts import create_tournament_post
        create_tournament_post(str(payload.club_id), tournament["id"], tournament["name"], str(payload.admin_id))
    except Exception as exc:
        print(f"[FEED] Failed to create tournament post for tournament={tournament['id']}: {exc}")

    tournament = _attach_link_info([tournament])[0]
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
    if payload.handicap_allowance not in VALID_HANDICAP_ALLOWANCES:
        raise InvalidHandicapAllowanceError(
            f"Handicap allowance must be one of: {', '.join(str(v) for v in sorted(VALID_HANDICAP_ALLOWANCES))}."
        )
    if payload.pairs_scoring_style not in VALID_PAIRS_SCORING_STYLES:
        raise InvalidPairsScoringStyleError(
            f"Pairs scoring style must be one of: {', '.join(sorted(VALID_PAIRS_SCORING_STYLES))}."
        )

    linked_tournament_id = str(payload.linked_tournament_id) if payload.linked_tournament_id else None
    _validate_link(str(tournament["club_id"]), tournament_id, linked_tournament_id)
    was_linked = bool(tournament.get("linked_tournament_id"))
    if linked_tournament_id and not was_linked:
        # Newly linking -- refuse to silently orphan any rounds/entrants
        # this tournament already owns; only a tournament with no data of
        # its own yet is safe to turn into a shadow (see
        # TournamentUpdate.linked_tournament_id's own comment).
        own_round = (
            supabase.table("tournament_rounds").select("id").eq("tournament_id", tournament_id).limit(1).execute()
        )
        own_entrant = (
            supabase.table("tournament_entrants").select("id").eq("tournament_id", tournament_id).limit(1).execute()
        )
        if own_round.data or own_entrant.data:
            raise InvalidLinkError(
                "This tournament already has its own rounds or entrants -- linking would hide them. "
                "Only a tournament with no data of its own yet can be linked."
            )

    # A shadow tournament plays its linked tournament's rounds and isn't
    # asked to submit any of its own -- see create_tournament's identical
    # check.
    if not linked_tournament_id and not payload.rounds:
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
            "handicap_allowance": payload.handicap_allowance,
            "pairs_scoring_style": payload.pairs_scoring_style,
            "linked_tournament_id": linked_tournament_id,
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
    # prompt, not something to silently preserve. For a (now-)shadow
    # tournament this deletes zero rows and payload.rounds is empty, so
    # the insert below is skipped entirely -- it never owned any rounds
    # to begin with.
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
    if round_rows:
        rounds_response = supabase.table("tournament_rounds").insert(round_rows).execute()
        rounds = _attach_course_names(rounds_response.data or [])
        for r in rounds:
            r["tee_times"] = []
    else:
        rounds = []

    updated_tournament = _attach_link_info([updated_tournament])[0]

    # A shadow shows its linked tournament's rounds/entrants instead of
    # the (empty) ones it owns itself -- same resolution as get_tournament.
    source_id = updated_tournament.get("linked_tournament_id")
    if source_id:
        rounds = _fetch_rounds_by_tournament([source_id]).get(source_id, [])
        entrants = _fetch_entrants_by_tournament([source_id]).get(source_id, [])
    else:
        entrants = _fetch_entrants_by_tournament([tournament_id]).get(tournament_id, [])

    return {
        **updated_tournament,
        "rounds": rounds,
        "entrants": entrants,
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

    tournaments = _attach_link_info(tournaments)

    # A shadow tournament's rounds/entrants live under its linked
    # tournament instead -- see _resolve_source_tournament_id. Batched on
    # the *resolved* ids (deduped) so a linked pair still costs one
    # rounds query and one entrants query total, not one per tournament.
    source_id_by_tournament = {t["id"]: (t.get("linked_tournament_id") or t["id"]) for t in tournaments}
    lookup_ids = list(set(source_id_by_tournament.values()))
    rounds_by_source = _fetch_rounds_by_tournament(lookup_ids)
    entrants_by_source = _fetch_entrants_by_tournament(lookup_ids)

    return [
        {
            **t,
            "rounds": rounds_by_source.get(source_id_by_tournament[t["id"]], []),
            "entrants": entrants_by_source.get(source_id_by_tournament[t["id"]], []),
        }
        for t in tournaments
    ]


def get_tournament(tournament_id: str) -> dict | None:
    response = supabase.table("tournaments").select("*").eq("id", tournament_id).maybe_single().execute()
    tournament = response.data if response is not None else None
    if not tournament:
        return None

    tournament = _attach_link_info([tournament])[0]

    # See _resolve_source_tournament_id -- a shadow's own rounds/entrants
    # queries would come back empty (it owns no rows in either table), so
    # this reads from whichever tournament it's linked to instead, same as
    # list_tournaments_for_club above.
    source_id = tournament.get("linked_tournament_id") or tournament_id
    rounds_by_tournament = _fetch_rounds_by_tournament([source_id])
    entrants_by_tournament = _fetch_entrants_by_tournament([source_id])

    return {
        **tournament,
        "rounds": rounds_by_tournament.get(source_id, []),
        "entrants": entrants_by_tournament.get(source_id, []),
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
    # handicap_allowance (50/75/100, default 100) scales every player's
    # handicap before it's used anywhere below -- the standard golf
    # competition "handicap limit" rule, applied once here rather than
    # threaded through _compute_leaderboard_line, so prior-round totals
    # and this round's line both reflect the same scaled figure. Pulled
    # in via the same embed pattern _PLAYER_EMBED uses elsewhere in this
    # file (tournament_rounds.tournament_id -> tournaments.id is a real
    # FK) rather than a second .execute() round trip -- one extra
    # network call per leaderboard poll isn't worth it when this page
    # already polls the endpoint on an interval.
    round_response = (
        supabase
        .table("tournament_rounds")
        .select("*, tournaments(handicap_allowance)")
        .eq("id", round_id)
        .maybe_single()
        .execute()
    )
    tournament_round = round_response.data if round_response is not None else None
    if not tournament_round or tournament_round["tournament_id"] != tournament_id:
        raise TournamentRoundNotFoundError("Round not found for this tournament.")

    handicap_allowance = (tournament_round.pop("tournaments", None) or {}).get("handicap_allowance") or 100

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
        # Admin override (see the migration comment on
        # tournament_entrants.handicap_override) takes the place of the
        # live lookup below, nothing more -- it's treated exactly like
        # the player's own full handicap would be, so it still goes
        # through this same tournament's allowance-percentage scaling
        # just below, same as every other entrant's. An admin fixing a
        # wrong handicap shouldn't also have to redo the allowance math
        # in their head to land on the right Comp. Hcp.
        if entrant.get("handicap_override") is not None:
            handicap_by_player[entrant["player_id"]] = entrant["handicap_override"]
            continue
        # Frozen at entry, not a live lookup -- standard competition
        # convention is that a player's handicap for the event is fixed
        # once the field is set, so a round played elsewhere (or a WHS
        # recalculation from finishing an earlier round of THIS
        # tournament) never moves their tournament scoring mid-event.
        # handicap_at_entry is captured once, at the moment they entered
        # (see enter_tournament/add_entrant in tournament_entrants.py),
        # via this exact same source-resolution -- so this isn't a
        # behavior change from what used to run here, just WHEN it's
        # evaluated: once, at entry, instead of live on every leaderboard
        # poll. Falls back to a live lookup only for the edge case of an
        # entrant row with no handicap_at_entry on record at all (a
        # player who entered before this field existed, or one whose
        # handicap was unavailable at entry time) so the leaderboard
        # still shows a number rather than silently dropping them.
        if entrant.get("handicap_at_entry") is not None:
            handicap_by_player[entrant["player_id"]] = entrant["handicap_at_entry"]
            continue
        source = get_effective_handicap_source(entrant["player_id"], entrant.get("handicap_source"))
        handicap_row = get_current_player_handicap(entrant["player_id"], source=source)
        handicap_by_player[entrant["player_id"]] = handicap_row["handicap"] if handicap_row else None

    if handicap_allowance != 100:
        handicap_by_player = {
            player_id: (handicap * handicap_allowance / 100 if handicap is not None else None)
            for player_id, handicap in handicap_by_player.items()
        }

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

def _per_hole_pair_metric(
    scores_by_hole: dict[int, int], holes_meta: dict[int, dict], handicap: float | None, style: str
) -> list[int | None]:
    """Raw (non-cumulative) per-hole metric for holes 1-18, None for any
    hole with no recorded strokes or no par on record -- the building
    block get_tournament_pairs_leaderboard needs for better-ball, where
    each hole's pair score is the *better of the two partners' metric on
    that specific hole* (higher for Stableford points, lower for nett/
    gross strokes -- see VALID_PAIRS_SCORING_STYLES). Not reusable from
    _compute_leaderboard_line's holes_stableford output above, which is a
    running cumulative total through each hole (right for an individual
    leaderboard line, but combining two cumulative totals hole-by-hole is
    a different, wrong number from combining the two per-hole metrics --
    the latter is the actual better-ball rule).

    "gross" is simply the raw strokes played. "nett"/"stableford" both
    need the same handicap-adjusted net strokes for the hole first --
    they just do something different with it once they have it (nett IS
    that net-strokes number; stableford converts it to points against
    par)."""
    values: list[int | None] = []
    for hole_number in range(1, 19):
        strokes = scores_by_hole.get(hole_number)
        hole = holes_meta.get(hole_number)
        par = hole.get("par") if hole else None
        if strokes is None or par is None:
            values.append(None)
            continue
        if style == "gross":
            values.append(strokes)
            continue
        stroke_index = hole.get("stroke_index")
        hcp_strokes = _hole_handicap_strokes(handicap, stroke_index)
        net_strokes = strokes - hcp_strokes
        if style == "nett":
            values.append(net_strokes)
        else:
            values.append(_stableford_points(net_strokes, par))
    return values


def _pair_round_metrics(
    pair: dict,
    metric_by_player: dict[str, list[int | None]],
    scores_by_player: dict[str, dict[int, int]],
    higher_wins: bool,
) -> dict:
    """One pair's better-ball combination for a single round -- extracted
    from get_tournament_pairs_leaderboard so get_tournament_pairs_overall_
    leaderboard (which needs the exact same per-round combination, just
    summed across every round instead of shown for one) doesn't duplicate
    the hole-by-hole "better of the two partners' metric" loop. See
    get_tournament_pairs_leaderboard's own docstring for the full
    better-ball/masking rules this implements -- unchanged here, just
    lifted out.

    metric_by_player/scores_by_player are one round's worth of data
    (built by the caller via _per_hole_pair_metric / _tournament_round_
    scores_by_player for that round's tee/holes_meta) -- this function
    itself has no notion of which round it's looking at."""
    a_id = str(pair["player_id_a"])
    b_id = str(pair["player_id_b"])
    a_metric = metric_by_player.get(a_id)
    b_metric = metric_by_player.get(b_id)
    a_strokes_by_hole = scores_by_player.get(a_id, {})
    b_strokes_by_hole = scores_by_player.get(b_id, {})

    holes_metric: list[int | None] = []
    a_holes_score: list[int | None] = []
    b_holes_score: list[int | None] = []
    running = 0
    thru = 0
    for i in range(18):
        hole_number = i + 1
        a = a_metric[i] if a_metric else None
        b = b_metric[i] if b_metric else None
        candidates = [v for v in (a, b) if v is not None]
        if not candidates:
            holes_metric.append(None)
        else:
            thru += 1
            running += max(candidates) if higher_wins else min(candidates)
            holes_metric.append(running)

        if higher_wins:
            a_contributed = a is not None and (b is None or a >= b)
            b_contributed = b is not None and (a is None or b > a)
        else:
            a_contributed = a is not None and (b is None or a <= b)
            b_contributed = b is not None and (a is None or b < a)
        a_holes_score.append(a_strokes_by_hole.get(hole_number) if a_contributed else None)
        b_holes_score.append(b_strokes_by_hole.get(hole_number) if b_contributed else None)

    return {
        "holes_metric": holes_metric,
        "total_metric": running,
        "thru": thru,
        "a_holes_score": a_holes_score,
        "b_holes_score": b_holes_score,
    }


def get_tournament_pairs_leaderboard(tournament_id: str, round_id: str) -> dict:
    """Better-ball pairs leaderboard for one round of a *pairs* tournament
    (format 2bbb/4bbb) -- same live-poll shape as get_tournament_leaderboard
    above, but grouped into this tournament's own tournament_pairs instead
    of one row per player. Each hole's pair score is the better of the two
    partners' individual per-hole metric on that specific hole (standard
    better-ball), combined across the round for the total -- which metric
    depends on the tournament's pairs_scoring_style (see
    VALID_PAIRS_SCORING_STYLES and _per_hole_pair_metric): Stableford
    points (higher wins each hole, higher total wins the pairs
    competition -- the original, still-default behavior), nett strokes,
    or gross strokes (lower wins each hole, lower total wins, for both).

    A pairs tournament linked to another (see tournaments.
    linked_tournament_id) has no entrants/rounds of its own -- this reads
    both through _resolve_source_tournament_id, same as get_tournament and
    get_tournament_leaderboard, so it works identically whether called
    with the pairs tournament's own id or (if it weren't linked) any
    other. A player currently NR on every hole of the round contributes no
    candidate score to their pair on any of those holes (see
    _tournament_round_scores_by_player -- an NR'd hole is simply absent
    from scores_by_player, same as an unplayed one); the pair still scores
    normally off their partner's holes. Only if *both* partners have no
    score for a given hole does the pair's own line show that hole as
    unplayed."""
    source_id = _resolve_source_tournament_id(tournament_id)

    round_response = (
        supabase
        .table("tournament_rounds")
        .select("*, tournaments(handicap_allowance, pairs_scoring_style)")
        .eq("id", round_id)
        .maybe_single()
        .execute()
    )
    tournament_round = round_response.data if round_response is not None else None
    if not tournament_round or tournament_round["tournament_id"] != source_id:
        raise TournamentRoundNotFoundError("Round not found for this tournament.")

    tournament_fields = tournament_round.pop("tournaments", None) or {}
    handicap_allowance = tournament_fields.get("handicap_allowance") or 100
    scoring_style = tournament_fields.get("pairs_scoring_style") or "stableford"
    if scoring_style not in VALID_PAIRS_SCORING_STYLES:
        scoring_style = "stableford"
    higher_wins = scoring_style == "stableford"

    entrants_by_tournament = _fetch_entrants_by_tournament([source_id])
    entrants = [e for e in entrants_by_tournament.get(source_id, []) if e["status"] == "confirmed"]

    handicap_by_player: dict[str, float | None] = {}
    for entrant in entrants:
        if entrant.get("handicap_override") is not None:
            handicap_by_player[entrant["player_id"]] = entrant["handicap_override"]
            continue
        # Frozen at entry, not a live lookup -- see get_tournament_
        # leaderboard's own copy of this same comment just above for the
        # full reasoning; same fallback for an entrant row with no
        # handicap_at_entry on record.
        if entrant.get("handicap_at_entry") is not None:
            handicap_by_player[entrant["player_id"]] = entrant["handicap_at_entry"]
            continue
        source = get_effective_handicap_source(entrant["player_id"], entrant.get("handicap_source"))
        handicap_row = get_current_player_handicap(entrant["player_id"], source=source)
        handicap_by_player[entrant["player_id"]] = handicap_row["handicap"] if handicap_row else None

    if handicap_allowance != 100:
        handicap_by_player = {
            player_id: (handicap * handicap_allowance / 100 if handicap is not None else None)
            for player_id, handicap in handicap_by_player.items()
        }

    holes_meta = _course_holes_meta(tournament_round["tee_id"])
    scores_by_player, _nr_by_player = _tournament_round_scores_by_player(round_id)

    metric_by_player: dict[str, list[int | None]] = {
        entrant["player_id"]: _per_hole_pair_metric(
            scores_by_player.get(entrant["player_id"], {}),
            holes_meta,
            handicap_by_player[entrant["player_id"]],
            scoring_style,
        )
        for entrant in entrants
    }

    pairs = []
    for pair in list_tournament_pairs(tournament_id):
        line = _pair_round_metrics(pair, metric_by_player, scores_by_player, higher_wins)
        pairs.append({
            "pair_id": pair["id"],
            "player_id_a": pair["player_id_a"],
            "player_id_b": pair["player_id_b"],
            "name": f"{pair.get('player_a_name') or 'Unknown'} & {pair.get('player_b_name') or 'Unknown'}",
            "thru": line["thru"],
            "holes_metric": line["holes_metric"],
            "total_metric": line["total_metric"],
            "players": [
                {"player_id": pair["player_id_a"], "name": pair.get("player_a_name") or "Unknown", "holes_score": line["a_holes_score"]},
                {"player_id": pair["player_id_b"], "name": pair.get("player_b_name") or "Unknown", "holes_score": line["b_holes_score"]},
            ],
        })

    # Leader-first -- higher total wins for Stableford, lower total wins
    # for nett/gross (a normal stroke-play comparison), unlike
    # get_tournament_leaderboard's players (which the frontend sorts
    # client-side by whichever of gross/nett/stableford it's currently
    # displaying) -- pairs only ever show the one number their
    # pairs_scoring_style picked, so sorting once here is enough.
    pairs.sort(key=lambda p: p["total_metric"], reverse=higher_wins)

    return {
        "round_id": round_id,
        "round_number": tournament_round["round_number"],
        "scoring_style": scoring_style,
        "holes": [
            {"hole_number": n, "par": holes_meta.get(n, {}).get("par")}
            for n in range(1, 19)
        ],
        "pairs": pairs,
    }


def get_tournament_pairs_overall_leaderboard(tournament_id: str) -> dict:
    """True cross-round OVERALL pairs standings for a pairs tournament
    (2bbb/4bbb) -- each pair's total_metric summed across *every* round of
    the tournament, not just one round at a time like
    get_tournament_pairs_leaderboard. This is the pairs equivalent of
    get_tournament_leaderboard's prior_gross/prior_nett/prior_stableford
    cross-round summation, which pairs never had until now -- the live
    Pairings tab only ever showed one round's totals via its per-round
    tabs, with no "Overall" resolution the way the individual leaderboard
    has (see its "overall" sentinel). Reuses _pair_round_metrics (the
    same better-ball per-round combination as get_tournament_pairs_
    leaderboard) once per round, per pair, then sums.

    Currently used by finalize_tournament to compute a pairs tournament's
    official final standings -- calling this once, after the last round,
    already gives the complete picture, no separate "last round" special
    case needed the way individual formats get one for free from their
    own prior-totals pattern.

    rounds_played is how many of the tournament's rounds this pair
    actually has at least one hole's score in (thru > 0 for that round) --
    included so a finalize-time edge case (a pair that missed a round
    entirely) is visible in the response rather than silently indistinct
    from a pair that played every round but scored 0."""
    source_id = _resolve_source_tournament_id(tournament_id)

    tournament_response = (
        supabase
        .table("tournaments")
        .select("handicap_allowance, pairs_scoring_style")
        .eq("id", source_id)
        .maybe_single()
        .execute()
    )
    tournament_fields = tournament_response.data if tournament_response is not None else None
    if not tournament_fields:
        raise TournamentNotFoundError("Tournament not found.")

    handicap_allowance = tournament_fields.get("handicap_allowance") or 100
    scoring_style = tournament_fields.get("pairs_scoring_style") or "stableford"
    if scoring_style not in VALID_PAIRS_SCORING_STYLES:
        scoring_style = "stableford"
    higher_wins = scoring_style == "stableford"

    entrants_by_tournament = _fetch_entrants_by_tournament([source_id])
    entrants = [e for e in entrants_by_tournament.get(source_id, []) if e["status"] == "confirmed"]

    # Same frozen-at-entry handicap resolution as get_tournament_pairs_
    # leaderboard -- one fixed value for the whole tournament, so it's
    # resolved once here rather than per round.
    handicap_by_player: dict[str, float | None] = {}
    for entrant in entrants:
        if entrant.get("handicap_override") is not None:
            handicap_by_player[entrant["player_id"]] = entrant["handicap_override"]
            continue
        if entrant.get("handicap_at_entry") is not None:
            handicap_by_player[entrant["player_id"]] = entrant["handicap_at_entry"]
            continue
        source = get_effective_handicap_source(entrant["player_id"], entrant.get("handicap_source"))
        handicap_row = get_current_player_handicap(entrant["player_id"], source=source)
        handicap_by_player[entrant["player_id"]] = handicap_row["handicap"] if handicap_row else None

    if handicap_allowance != 100:
        handicap_by_player = {
            player_id: (handicap * handicap_allowance / 100 if handicap is not None else None)
            for player_id, handicap in handicap_by_player.items()
        }

    rounds_response = (
        supabase
        .table("tournament_rounds")
        .select("id, round_number, tee_id")
        .eq("tournament_id", source_id)
        .order("round_number")
        .execute()
    )
    tournament_rounds = rounds_response.data or []

    pairs_meta = list_tournament_pairs(tournament_id)
    total_by_pair = {pair["id"]: 0 for pair in pairs_meta}
    rounds_played_by_pair = {pair["id"]: 0 for pair in pairs_meta}

    for tournament_round in tournament_rounds:
        holes_meta = _course_holes_meta(tournament_round["tee_id"])
        scores_by_player, _nr_by_player = _tournament_round_scores_by_player(tournament_round["id"])
        metric_by_player = {
            entrant["player_id"]: _per_hole_pair_metric(
                scores_by_player.get(entrant["player_id"], {}),
                holes_meta,
                handicap_by_player[entrant["player_id"]],
                scoring_style,
            )
            for entrant in entrants
        }
        for pair in pairs_meta:
            line = _pair_round_metrics(pair, metric_by_player, scores_by_player, higher_wins)
            total_by_pair[pair["id"]] += line["total_metric"]
            if line["thru"] > 0:
                rounds_played_by_pair[pair["id"]] += 1

    pairs = [
        {
            "pair_id": pair["id"],
            "player_id_a": pair["player_id_a"],
            "player_id_b": pair["player_id_b"],
            "name": f"{pair.get('player_a_name') or 'Unknown'} & {pair.get('player_b_name') or 'Unknown'}",
            "rounds_played": rounds_played_by_pair[pair["id"]],
            "total_metric": total_by_pair[pair["id"]],
        }
        for pair in pairs_meta
    ]
    # Leader-first, same win direction per scoring style as the per-round
    # leaderboard's own sort.
    pairs.sort(key=lambda p: p["total_metric"], reverse=higher_wins)

    return {
        "scoring_style": scoring_style,
        "round_count": len(tournament_rounds),
        "pairs": pairs,
    }


def _all_tournament_rounds_completed(tournament_id: str) -> tuple[bool, str | None]:
    """Precondition check for finalize_tournament -- every tournament_
    rounds row (one per round-in-the-comp -- date/course/tee/group_size,
    see TournamentRoundCreate) needs at least one linked `rounds` row
    (rounds.tournament_round_id -- one per tee-time group that actually
    played that day), and every one of those needs status == "completed".
    A round only reaches "completed" once every accepted player in that
    group has signed off (see sign_off_round in backend/services/
    rounds.py) -- so this transitively requires full sign-off across the
    whole field, not just that scores were entered.

    Resolves through _resolve_source_tournament_id first so calling this
    with either a source tournament's id or its pairs shadow's id checks
    the same underlying rounds (a shadow owns none of its own).

    Returns (True, None) once every round for every group is done, or
    (False, <reason>) naming the first round that isn't -- either never
    started at all, or started but not yet fully signed off -- so the
    Finalize button's error message can tell the admin specifically what's
    still outstanding instead of a generic "not ready" toast."""
    source_id = _resolve_source_tournament_id(tournament_id)

    rounds_response = (
        supabase
        .table("tournament_rounds")
        .select("id, round_number, round_date")
        .eq("tournament_id", source_id)
        .order("round_number")
        .execute()
    )
    tournament_rounds = rounds_response.data or []
    if not tournament_rounds:
        return False, "This tournament has no rounds set up yet."

    for tournament_round in tournament_rounds:
        label = f"Round {tournament_round['round_number']} ({tournament_round['round_date']})"

        played_response = (
            supabase
            .table("rounds")
            .select("id, status")
            .eq("tournament_round_id", tournament_round["id"])
            .execute()
        )
        played_rounds = played_response.data or []
        if not played_rounds:
            return False, f"{label} hasn't been started yet."

        not_completed = [r for r in played_rounds if r.get("status") != "completed"]
        if not_completed:
            return (
                False,
                f"{label} is still in progress -- every group has to finish and be signed off before finalizing.",
            )

    return True, None


def _winner_summary_from_ranked(winners: list[dict], separator: str = " & ") -> str | None:
    """Joins one or more tied winners' display names into the plain
    winner_summary string persisted on the tournament row -- a single
    winner is just their own name, an exact tie joins every tied name
    with separator. Shared by both the individual and pairs branches of
    finalize_tournament below (pairs passes " / " instead of the default
    " & " since a pair's own `name` already contains "Player A & Player
    B", so joining two TIED pairs with the same separator would read as
    one ambiguous four-person string)."""
    if not winners:
        return None
    return separator.join(w["name"] for w in winners)


def finalize_tournament(tournament_id: str, payload: TournamentFinalizeRequest) -> dict:
    """Admin-only, one-way action: locks this tournament's official final
    standings once every round has been played and fully signed off (see
    _all_tournament_rounds_completed). Persists a snapshot (final_
    leaderboard) computed once, right now, plus a plain winner_summary
    display string and event_date (the last round's own date, not
    finalized_at -- an admin might not click Finalize until well after
    the tournament actually finished; see finalize_tournament.sql). See
    TournamentAlreadyFinalizedError -- there's no un-finalize, a second
    call just refuses rather than silently recomputing.

    Operates on tournament_id's own row, not necessarily its resolved
    source -- per the user's explicit choice, a pairs tournament linked
    to an individual one finalizes independently, with its own Finalize
    action, its own final_leaderboard, and its own winner, never tied to
    its linked tournament's own finalize state. Round/score data is still
    read through _resolve_source_tournament_id (a shadow owns none of its
    own), same as every other read in this file -- only the *write*
    target (which tournament row gets finalized_at set) is tournament_id
    itself.

    format alone decides which branch runs: 2bbb/4bbb use the new
    get_tournament_pairs_overall_leaderboard (#413) for a true cross-round
    pairs standings; every other format calls get_tournament_leaderboard
    for the *last* round only, which already includes each player's full
    cross-round prior_gross/prior_nett/prior_stableford totals -- no new
    aggregation needed there, that leaderboard was always cumulative."""
    tournament_response = supabase.table("tournaments").select("*").eq("id", tournament_id).maybe_single().execute()
    tournament = tournament_response.data if tournament_response is not None else None
    if not tournament:
        raise TournamentNotFoundError("Tournament not found.")

    club = _get_club(tournament["club_id"])
    if not club or str(club.get("club_admin")) != str(payload.admin_id):
        raise NotClubAdminError("Only this club's admin can finalize tournaments.")

    if tournament.get("finalized_at"):
        raise TournamentAlreadyFinalizedError("This tournament has already been finalized.")

    ready, reason = _all_tournament_rounds_completed(tournament_id)
    if not ready:
        raise TournamentNotReadyToFinalizeError(reason)

    source_id = _resolve_source_tournament_id(tournament_id)
    rounds_response = (
        supabase
        .table("tournament_rounds")
        .select("id, round_number, round_date")
        .eq("tournament_id", source_id)
        .order("round_number")
        .execute()
    )
    tournament_rounds = rounds_response.data or []
    # _all_tournament_rounds_completed above already refused to get this
    # far if tournament_rounds were empty, so there's always a last round
    # here.
    last_round = tournament_rounds[-1]
    event_date = last_round["round_date"]

    if tournament["format"] in ("2bbb", "4bbb"):
        final_leaderboard = get_tournament_pairs_overall_leaderboard(tournament_id)
        pairs = final_leaderboard["pairs"]
        if not pairs:
            winner_summary = None
        else:
            higher_wins = final_leaderboard["scoring_style"] == "stableford"
            best_value = pairs[0]["total_metric"]
            winners = [p for p in pairs if p["total_metric"] == best_value]
            # Pair names already read "Player A & Player B" -- " / "
            # keeps a tie between two whole pairs unambiguous rather than
            # chaining a second " & " onto the same string.
            winner_summary = _winner_summary_from_ranked(winners, separator=" / ")
    else:
        final_leaderboard = get_tournament_leaderboard(tournament_id, last_round["id"])
        all_players = final_leaderboard["players"]
        # NR'd players are excluded from winner contention (same as they're
        # sorted to the bottom of the live leaderboard) -- but if literally
        # everyone in the field is NR, fall back to ranking them anyway
        # rather than leaving winner_summary blank for a tournament that
        # did get played.
        ranked_players = [p for p in all_players if not p.get("is_nr")] or all_players
        if not ranked_players:
            winner_summary = None
        else:
            format_metric = {
                "stableford": ("total_stableford", True),
                "net": ("total_nett", False),
            }
            metric_key, higher_wins = format_metric.get(tournament["format"], ("total_gross", False))
            best_value = (
                max(p[metric_key] for p in ranked_players)
                if higher_wins
                else min(p[metric_key] for p in ranked_players)
            )
            winners = [p for p in ranked_players if p[metric_key] == best_value]
            winner_summary = _winner_summary_from_ranked(winners)

    update_response = (
        supabase
        .table("tournaments")
        .update({
            "finalized_at": datetime.now(timezone.utc).isoformat(),
            "finalized_by": str(payload.admin_id),
            "final_leaderboard": final_leaderboard,
            "winner_summary": winner_summary,
            "event_date": event_date,
        })
        .eq("id", tournament_id)
        .execute()
    )
    updated_tournament = update_response.data[0]

    # Best-effort, same "never let a feed post block the real action"
    # convention as create_tournament's own create_tournament_post call --
    # finalize itself has already fully succeeded above by this point.
    # Local import for the same avoid-a-module-scope-circular-import
    # reason as that call site.
    try:
        from backend.services.club_posts import create_tournament_finalized_post
        create_tournament_finalized_post(
            tournament["club_id"],
            tournament_id,
            tournament["name"],
            tournament["format"],
            winner_summary,
            final_leaderboard,
        )
    except Exception as exc:
        print(f"[FEED] Failed to create finalized-tournament post for tournament={tournament_id}: {exc}")

    return _attach_link_info([updated_tournament])[0]


def get_tournament_winner(tournament_id: str) -> dict | None:
    """Plain read of a tournament's own finalize fields -- name, format,
    when/who finalized it, the locked final_leaderboard snapshot, and
    winner_summary. No linked-tournament resolution here (unlike every
    other read in this file) since finalize is independent per tournament
    -- see finalize_tournament's own docstring on that choice; a shadow
    and its source can each be finalized (or not) entirely on their own.
    Returns None both when tournament_id doesn't exist at all and when it
    exists but simply isn't finalized yet -- the router maps either to a
    404, since "nothing to show on a Winners page" is the same response
    either way from the frontend's perspective."""
    response = (
        supabase
        .table("tournaments")
        .select("id, name, format, finalized_at, finalized_by, final_leaderboard, winner_summary, event_date")
        .eq("id", tournament_id)
        .maybe_single()
        .execute()
    )
    tournament = response.data if response is not None else None
    if not tournament or not tournament.get("finalized_at"):
        return None
    return tournament


def get_club_tournament_history(club_id: str) -> list[dict]:
    """Every finalized tournament at this club -- tournament name, format,
    winner_summary, and event_date (the History page's "year" column
    reads off this, not finalized_at -- see finalize_tournament.sql) --
    newest first. Filtered client-side on finalized_at being set rather
    than a `.not_.is_()` filter in the query, so this doesn't depend on
    exactly how this project's supabase-py version expresses "IS NOT
    NULL"; club tournament counts are small enough that fetching every
    row and filtering here costs nothing meaningful."""
    response = (
        supabase
        .table("tournaments")
        .select("id, name, format, winner_summary, event_date, finalized_at")
        .eq("club_id", club_id)
        .order("event_date", desc=True)
        .execute()
    )
    tournaments = response.data or []
    return [t for t in tournaments if t.get("finalized_at")]