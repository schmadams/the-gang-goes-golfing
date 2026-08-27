# target path: backend/services/calendar.py (new file)
from backend.database import supabase

# The three chip colors/types the frontend's month grid (pages/calendar.py)
# distinguishes between -- kept as plain strings rather than an enum for
# the same reason every other status/category string in this app is (e.g.
# notifications.py's CATEGORIES) -- this talks to the frontend over plain
# JSON, not shared Python types.
#
# "round"      -- a completed round this player played in (the past).
# "scheduled"  -- a tournament round with a published tee time (the
#                 future, and you know exactly when you're teeing off).
# "tournament" -- a tournament round this player's a confirmed entrant
#                 for, but tee times haven't been generated yet (the
#                 future, date known, time not yet).
#
# A given tournament round only ever shows up as ONE of "scheduled" or
# "tournament" for a given player, never both -- see the dedupe logic in
# get_calendar_events below.


def get_calendar_events(player_id: str) -> list[dict]:
    """Every event that belongs on this player's personal calendar --
    historic rounds, scheduled tee times, and tournament entries still
    awaiting a published tee time -- merged into one flat list, sorted by
    date. pages/calendar.py buckets these by date client-side (well,
    server-side in layout(), there's no actual client-side JS here) to
    build the month grid; this function doesn't know anything about
    months or grids, it just answers "what happened or is happening, and
    when."""
    events = []

    # -- Historic (completed) rounds --------------------------------
    # Reuses list_player_rounds wholesale rather than re-querying rounds/
    # round_players/course hydration a second time -- that function
    # already does exactly the club/course-name hydration this needs, for
    # the Rounds History panel and Scoring History page. limit=200 is far
    # more than any realistic player's completed-round history; bump it
    # if that ever stops being true rather than paginating the calendar
    # itself against it.
    from backend.services.rounds import list_player_rounds

    for round_row in list_player_rounds(player_id, limit=200):
        if round_row.get("status") != "completed":
            continue
        # rounds has no round_date column -- completed_at is the
        # timestamp this app already treats as "the date this round
        # happened" everywhere else (see e.g. list_player_rounds' own
        # neighbors get_player_analysis/get_club_player_comparison in
        # this file, both slicing completed_at the same way). Without
        # this, every completed round was silently dropped from the
        # calendar (round_row.get("round_date") is always None).
        event_date = (round_row.get("completed_at") or "")[:10]
        if not event_date:
            continue
        course_bits = [b for b in [round_row.get("club_name"), round_row.get("course_name")] if b]
        events.append({
            "date": event_date,
            "type": "round",
            "title": " – ".join(course_bits) or "Round",
            "subtitle": f"{round_row['total_strokes']} strokes" if round_row.get("total_strokes") else None,
            "url": "/analysis",
        })

    # -- Scheduled tee times (tournament rounds with a published time) --
    from backend.services.tournament_tee_times import list_scheduled_tee_times_for_player

    scheduled = list_scheduled_tee_times_for_player(player_id)
    # Keys this player already has a real tee time for -- checked below
    # so the same tournament round never shows up a second time as a
    # plain "entered" event (see the merge-behavior call this was built
    # around: one chip per day, with the tee time once it's published,
    # not two overlapping chips for the same round).
    scheduled_keys = set()
    for slot in scheduled:
        event_date = slot.get("round_date")
        if not event_date:
            continue
        scheduled_keys.add((slot.get("tournament_id"), event_date))
        events.append({
            "date": event_date,
            "type": "scheduled",
            "title": slot.get("tournament_name") or "Tournament round",
            "subtitle": f"Tee time {slot['tee_time']}" if slot.get("tee_time") else None,
            "url": (
                f"/clubs/{slot['club_slug']}/tournaments/{slot['tournament_id']}"
                if slot.get("club_slug") and slot.get("tournament_id")
                else None
            ),
        })

    # -- Confirmed tournament entries without a tee time yet ------------
    entrants_response = (
        supabase
        .table("tournament_entrants")
        .select("tournament_id")
        .eq("player_id", player_id)
        .eq("status", "confirmed")
        .execute()
    )
    tournament_ids = list({row["tournament_id"] for row in (entrants_response.data or [])})

    if tournament_ids:
        rounds_response = (
            supabase
            .table("tournament_rounds")
            .select("*")
            .in_("tournament_id", tournament_ids)
            .execute()
        )
        tournaments_response = (
            supabase.table("tournaments").select("id, name, club_id").in_("id", tournament_ids).execute()
        )
        tournaments_by_id = {t["id"]: t for t in (tournaments_response.data or [])}

        club_ids = list({t["club_id"] for t in tournaments_by_id.values() if t.get("club_id")})
        clubs_by_id = {}
        if club_ids:
            clubs_response = supabase.table("clubs").select("id, slug").in_("id", club_ids).execute()
            clubs_by_id = {c["id"]: c for c in (clubs_response.data or [])}

        for tournament_round in (rounds_response.data or []):
            event_date = tournament_round.get("round_date")
            tournament_id = tournament_round.get("tournament_id")
            if not event_date or (tournament_id, event_date) in scheduled_keys:
                continue

            tournament = tournaments_by_id.get(tournament_id) or {}
            club = clubs_by_id.get(tournament.get("club_id")) or {}
            club_slug = club.get("slug")

            events.append({
                "date": event_date,
                "type": "tournament",
                "title": tournament.get("name") or "Tournament",
                "subtitle": (
                    f"Round {tournament_round['round_number']}"
                    if tournament_round.get("round_number")
                    else None
                ),
                "url": f"/clubs/{club_slug}/tournaments/{tournament_id}" if club_slug else None,
            })

    events.sort(key=lambda e: e["date"])
    return events