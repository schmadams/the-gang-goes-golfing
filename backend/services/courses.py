# target path: backend/services/courses.py (full replacement)
import os
import time
from contextlib import contextmanager

import requests

from backend.database import supabase

RAPIDAPI_HOST = "uk-golf-course-data-api.p.rapidapi.com"
# Requests must go through RapidAPI's own gateway domain, not the
# provider's origin server directly (uk-golf-api.vercel.app) -- the origin
# rejects direct traffic with "This API is only accessible through
# RapidAPI", regardless of what their docs' example URLs show.
RAPIDAPI_BASE_URL = f"https://{RAPIDAPI_HOST}"


class ExternalApiError(Exception):
    """
    Raised when the UK Golf API rejects a request. Carries the actual
    status code + response body, since RapidAPI puts the real reason
    (not subscribed, invalid key, rate limited, etc.) in the body --
    response.raise_for_status() alone throws that away.
    """
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"UK Golf API returned {status_code}: {body}")


@contextmanager
def _timed(label: str, source: str):
    """
    Logs how long a single external API call or Supabase query took, tagged
    "external API" (UK Golf API, counts against the monthly quota) or
    "database" (our own Supabase), so we can tell from the console alone
    which layer is actually slow instead of guessing. Prints unconditionally
    (matching the rest of this file's print-based diagnostics) so it shows
    up in the same PyCharm console output already being used to debug this.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[TIMING] {source:12s} {elapsed_ms:8.1f}ms  {label}")


def _rapidapi_headers() -> dict:
    api_key = os.environ.get("UK_GOLF_API_KEY")

    if not api_key:
        raise EnvironmentError(
            "Missing UK_GOLF_API_KEY. Get a free key at "
            "https://rapidapi.com/raznut303/api/uk-golf-course-data-api "
            "and add it to your .env file."
        )

    return {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": RAPIDAPI_HOST}


def search_local_courses(query: str = "", limit: int = 3000) -> list[dict]:
    """
    List courses we've already cached. With a query, filters by name. With
    no query, returns everything -- used to preload the full list once on
    page load, so the home-course dropdown can filter client-side instead
    of round-tripping to the backend on every keystroke. Free either way —
    this never touches the external API or its monthly quota.

    Supabase/PostgREST used to cap any single request at 1000 rows
    regardless of .limit() -- confirmed empirically (a request for
    "everything" silently came back with exactly 1000 rows, cutting off
    alphabetically once the cached club list grew past that), which we
    worked around by paging through in batches of 1000. The project's Max
    Rows setting (Project Settings -> API -> Max Rows) is now raised to
    10,000 -- comfortably above the current cached club count -- so a
    single request is sufficient again and the extra round trip (and its
    ~200-300ms of added latency) is gone. If the cached count ever
    approaches 10,000, either raise Max Rows further or bring back .range()
    pagination.
    """
    query_builder = supabase.table("courses").select("*").order("club_name")

    if query:
        escaped = query.replace(",", " ").replace("%", "")
        query_builder = query_builder.or_(
            f"club_name.ilike.%{escaped}%,course_name.ilike.%{escaped}%"
        )
        label = f"search_local_courses(query={query!r})"
    else:
        label = "search_local_courses(all)"

    with _timed(label, "database"):
        response = query_builder.limit(limit).execute()

    return response.data or []


def search_local_clubs(query: str = "", limit: int = 3000) -> list[dict]:
    """
    Distinct list of real-world golf club names (not this app's own
    "clubs" concept -- see backend/models/club.py for that), for the Start
    New Round club-search step. The courses table is one row per COURSE,
    denormalized with its own club_name copied onto every row (some clubs
    have several courses sharing one club_name, e.g. East/West), so a
    plain select here would list "Wentworth" three times, once per course.
    PostgREST has no server-side DISTINCT through supabase-py's query
    builder, so this fetches matching rows the same way
    search_local_courses does and dedupes club_name in Python instead --
    fine at this table's size, and keeps whichever row's county/postcode
    was seen first as a representative location hint for that club_name.
    """
    query_builder = supabase.table("courses").select("club_name, county, postcode").order("club_name")

    if query:
        escaped = query.replace(",", " ").replace("%", "")
        query_builder = query_builder.ilike("club_name", f"%{escaped}%")
        label = f"search_local_clubs(query={query!r})"
    else:
        label = "search_local_clubs(all)"

    with _timed(label, "database"):
        response = query_builder.limit(limit).execute()

    rows = response.data or []
    deduped = {}
    for row in rows:
        name = row.get("club_name")
        if name and name not in deduped:
            deduped[name] = row

    return list(deduped.values())


def list_courses_for_club(club_name: str) -> list[dict]:
    """
    Every cached course under one exact real-world club name -- the second
    step of the Start New Round club -> course -> tees flow, once a club's
    been picked from a search_local_clubs result. Exact match (not ilike)
    since club_name here always comes from that earlier result, not free
    typing.

    This is the FIRST place a club's course list is ever shown to a
    player, before any course has been picked -- so it's also where
    sibling-course discovery has to happen, not just import_course(). The
    regions crawl (scripts/import_courses.py) only ever leaves one
    nameless placeholder row per club; a two-course club used to only ever
    surface its first course here, forever, because nothing before this
    fix triggered the "learn every course this club has" API call until
    AFTER a specific course was already selected. Runs at most once per
    club (see club_courses_discovered on the returned rows) -- a cache
    miss costs one extra request, a hit costs nothing.
    """
    query_builder = (
        supabase.table("courses")
        .select("*")
        .eq("club_name", club_name)
        .order("course_name")
    )

    with _timed(f"list_courses_for_club(club_name={club_name!r})", "database"):
        response = query_builder.execute()

    rows = response.data or []
    if not rows:
        return rows

    external_club_id = rows[0].get("external_club_id")
    if external_club_id and not any(row.get("club_courses_discovered") for row in rows):
        try:
            _ensure_club_courses_discovered(club_name, external_club_id, rows)
        except ExternalApiError:
            # Discovery failing shouldn't break the dropdown -- fall back
            # to whatever's already cached rather than raising out of a
            # read endpoint.
            return rows

        with _timed(f"list_courses_for_club(club_name={club_name!r}) re-fetch after discovery", "database"):
            response = query_builder.execute()
        rows = response.data or []

    return rows


def search_external_clubs(query: str) -> list[dict]:
    """
    NOTE: the UK Golf API has no free-text "search by club name" endpoint --
    their tutorial implies one exists via ?search=, but the real /clubs
    endpoint only filters by country/county/postcode/type/rating/facilities/
    green fees (confirmed empirically: a real club name returned zero
    results). Left as a stub that fails loudly rather than silently
    returning no matches. After running scripts/import_courses.py, local
    search covers every UK club anyway, so this fallback isn't needed for
    the home-course field. Revisit if we ever want a live top-up (e.g. a
    club added after the crawl) -- would need to filter by county instead.
    """
    raise ExternalApiError(
        501,
        "Live club search isn't available on this API. Run "
        "scripts/import_courses.py to cache all UK clubs locally instead.",
    )


def get_course(course_id: str) -> dict | None:
    """
    NOTE: this makes 2 + (1 per tee) Supabase round trips -- a real N+1
    pattern once a course has 3-4 tees. Each is individually timed below so
    that pattern shows up clearly in the logs rather than being hidden
    behind one aggregate number.
    """
    with _timed(f"get_course({course_id}): fetch course row", "database"):
        course_response = (
            supabase.table("courses").select("*").eq("id", course_id).maybe_single().execute()
        )

    if course_response is None:
        return None

    course = course_response.data
    if not course:
        return None

    with _timed(f"get_course({course_id}): fetch tees", "database"):
        tees_response = (
            supabase.table("course_tees").select("*").eq("course_id", course_id).execute()
        )
    tees = tees_response.data or []

    # Defensive de-dup by name -- a handful of courses ended up with the
    # same tee (e.g. two "White tees" entries) inserted twice, either
    # because the external API's own tee_sets list had a duplicate, or an
    # earlier import ran its insert loop again for a course that already
    # had tees cached. Keeping the first occurrence rather than crashing
    # or just showing the duplicate means already-affected courses
    # self-heal here instead of needing a manual DB cleanup, and this is
    # the one place every course-detail read (dropdown, scorecard, etc.)
    # goes through.
    seen_tee_names = set()
    deduped_tees = []
    for tee in tees:
        name_key = (tee.get("name") or "").strip().lower()
        if name_key and name_key in seen_tee_names:
            continue
        seen_tee_names.add(name_key)
        deduped_tees.append(tee)
    tees = deduped_tees

    for tee in tees:
        with _timed(f"get_course({course_id}): fetch holes for tee {tee['id']}", "database"):
            holes_response = (
                supabase
                .table("course_holes")
                .select("*")
                .eq("tee_id", tee["id"])
                .order("hole_number")
                .execute()
            )
        tee["holes"] = holes_response.data or []

    course["tees"] = tees
    return course


def _unwrap_object(payload, *expected_keys):
    """
    Some endpoints on this API wrap results in {"data": ...} (per their own
    code samples); others (confirmed for /regions, /clubs/{id}, and
    /courses/{id}) return the object directly. Try the bare interpretation
    first -- if it has the keys we actually expect, use it -- otherwise
    fall back to unwrapping "data".
    """
    if isinstance(payload, dict) and any(key in payload for key in expected_keys):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def _upsert_course_row(existing_row: dict | None, payload: dict) -> dict:
    """
    Writes whatever we just learned straight to the courses row -- update if
    we already have one, insert if this is the very first thing we've ever
    learned about this club. Called after every individual external API
    response (club lookup, then course detail) rather than once at the end,
    so a request's result is saved even if a *later* request in the same
    import_course() call fails or the process is interrupted. With a 200
    requests/month budget, no successful response can be allowed to go to
    waste.
    """
    if existing_row:
        with _timed(f"_upsert_course_row: update courses row {existing_row['id']}", "database"):
            return (
                supabase
                .table("courses")
                .update(payload)
                .eq("id", existing_row["id"])
                .execute()
                .data[0]
            )
    with _timed("_upsert_course_row: insert courses row", "database"):
        return supabase.table("courses").insert(payload).execute().data[0]


def _fetch_and_store_course_detail(course_row: dict, course_id: str) -> dict:
    """
    Spends exactly one request: GET /courses/{course_id}, which (confirmed
    via a live curl test) already returns full tee_sets + holes directly on
    the bare object. Persists course-level fields immediately, then tees/
    holes, so this response is fully saved even if some other step in the
    caller fails afterwards.
    """
    with _timed(f"GET /courses/{course_id}", "external API"):
        course_detail_response = requests.get(
            f"{RAPIDAPI_BASE_URL}/courses/{course_id}",
            headers=_rapidapi_headers(),
        )
    if course_detail_response.status_code != 200:
        raise ExternalApiError(course_detail_response.status_code, course_detail_response.text)

    course_detail = _unwrap_object(course_detail_response.json(), "tee_sets", "name")

    if not course_detail.get("tee_sets"):
        # Shouldn't happen based on the confirmed real shape, but keep a
        # diagnostic in case a different club's course omits tee data.
        print(f"  No tee_sets found for course {course_id} after unwrap. "
              f"Parsed: {course_detail} | Raw: {course_detail_response.text[:1000]}")

    course_row = _upsert_course_row(course_row, {
        "course_name": course_row.get("course_name") or course_detail.get("name"),
        "designed_by": course_detail.get("designed_by"),
        "year_opened": course_detail.get("year_opened"),
    })

    with _timed(f"fetch existing tee names for course {course_row['id']}", "database"):
        existing_tee_names = {
            (row.get("name") or "").strip().lower()
            for row in (
                supabase
                .table("course_tees")
                .select("name")
                .eq("course_id", course_row["id"])
                .execute()
                .data
                or []
            )
        }

    for tee_set in course_detail.get("tee_sets", []):
        # "name" is always null on this API -- the tee is actually
        # identified by "colour" (e.g. "black", "red"). Title-case it so it
        # reads naturally wherever we display tee.name (e.g. "Black tees").
        tee_name = tee_set.get("name") or (tee_set.get("colour") or "Unknown").title()

        # Guards against inserting the same tee twice -- e.g. this
        # function getting called again for a course that already has its
        # tees cached (a retried request, or a course_detail response that
        # happened to list the same tee_set more than once), which is how
        # a handful of courses ended up with duplicate tees in the
        # dropdown (get_course() also de-dupes defensively on read, but
        # not inserting the duplicate row in the first place is the real
        # fix).
        name_key = tee_name.strip().lower()
        if name_key in existing_tee_names:
            continue
        existing_tee_names.add(name_key)

        with _timed(f"insert course_tees row ({tee_name})", "database"):
            tee_row = (
                supabase
                .table("course_tees")
                .insert({
                    "course_id": course_row["id"],
                    "name": tee_name,
                    "par": tee_set.get("par"),
                    "course_rating": tee_set.get("course_rating"),
                    "slope_rating": tee_set.get("slope_rating"),
                })
                .execute()
                .data[0]
            )

        holes = [
            {
                "tee_id": tee_row["id"],
                "hole_number": hole["hole_number"],
                "par": hole["par"],
                "yardage": hole.get("yardage"),
                "stroke_index": hole.get("stroke_index"),
            }
            for hole in tee_set.get("holes", [])
        ]
        if holes:
            with _timed(f"insert course_holes rows for tee {tee_row['id']} ({len(holes)} holes)", "database"):
                supabase.table("course_holes").insert(holes).execute()

    return get_course(course_row["id"])


def _ensure_club_courses_discovered(club_name: str, external_club_id: str, existing_rows: list[dict]) -> list[dict]:
    """
    Spends exactly one request (GET /clubs/{id}) the FIRST time any course
    from this club is looked at -- whether that's a player opening the
    course dropdown for a club (list_courses_for_club, before any course
    has been picked) or picking a specific course to import
    (import_course). Before this existed, discovery only ever happened
    from inside import_course, AFTER a specific course had already been
    resolved -- so the by-club dropdown itself (a pure DB read) could only
    ever show whichever single course a previous import happened to touch,
    and once that one course had a cached scorecard, import_course's own
    "already cached" early-return meant the /clubs lookup (and therefore
    the loop that gives every sibling course its own row) never ran again
    for that club, ever. A two-course club could permanently get stuck
    showing only its first course. This function is the one place that
    now runs that discovery, and it runs it BEFORE a course is resolved,
    not after.

    Idempotent per club: every row this touches gets
    club_courses_discovered=True, and both callers skip calling this again
    once any row for the club already has that flag set -- so a club's
    sibling courses are only ever fetched from the live API once, no
    matter how many times players browse or start rounds there afterward.
    """
    with _timed(f"GET /clubs/{external_club_id}", "external API"):
        club_response = requests.get(
            f"{RAPIDAPI_BASE_URL}/clubs/{external_club_id}",
            headers=_rapidapi_headers(),
        )
    if club_response.status_code != 200:
        raise ExternalApiError(club_response.status_code, club_response.text)

    club = _unwrap_object(club_response.json(), "id", "name", "courses")

    # Save what this call taught us about the club itself immediately, even
    # if the course list below turns out to be empty -- this request isn't
    # wasted either way.
    placeholder_row = existing_rows[0] if existing_rows else None
    placeholder_row = _upsert_course_row(placeholder_row, {
        "external_club_id": external_club_id,
        "club_name": club.get("name") or club_name,
        "county": club.get("county") or (placeholder_row or {}).get("county"),
        "postcode": club.get("postcode") or (placeholder_row or {}).get("postcode"),
    })

    club_courses = club.get("courses") or []
    if not club_courses:
        # Nothing more to discover -- mark the placeholder done so this
        # club is never re-queried, and leave it as the one (nameless) row
        # it's always been.
        return [_upsert_course_row(placeholder_row, {"club_courses_discovered": True})]

    updated_rows = []
    placeholder_upgraded = False

    for club_course in club_courses:
        course_id = club_course.get("id")
        course_name = club_course.get("name")

        matching_existing = next(
            (row for row in existing_rows if row.get("external_course_id") == course_id),
            None,
        )
        if matching_existing:
            updated_rows.append(_upsert_course_row(matching_existing, {
                "club_courses_discovered": True,
            }))
            continue

        if not placeholder_upgraded and not placeholder_row.get("external_course_id"):
            # Upgrade the club-level placeholder row (from the regions
            # crawl, or just created above) into this first real course,
            # instead of leaving it dangling as a nameless duplicate.
            updated_rows.append(_upsert_course_row(placeholder_row, {
                "external_course_id": course_id,
                "course_name": course_name,
                "club_courses_discovered": True,
            }))
            placeholder_upgraded = True
            continue

        updated_rows.append(_upsert_course_row(None, {
            "external_club_id": external_club_id,
            "club_name": placeholder_row.get("club_name"),
            "county": placeholder_row.get("county"),
            "postcode": placeholder_row.get("postcode"),
            "external_course_id": course_id,
            "course_name": course_name,
            "club_courses_discovered": True,
        }))

    return updated_rows


def import_course(details: dict) -> dict | None:
    """
    Given a cached club (external_club_id/club_name/county/postcode, from
    the regions crawl), fetch its course list + full scorecard from the
    live API and cache the full result locally (courses / course_tees /
    course_holes).

    Every external API response is written to the DB the instant it comes
    back (via _upsert_course_row / _fetch_and_store_course_detail) instead
    of being held in memory until the whole import succeeds. That matters
    because the free tier is 200 requests/month total: if we already know
    external_course_id for a club (saved from a previous, possibly
    incomplete, import attempt), we skip the /clubs lookup entirely and
    spend exactly one request on /courses/{course_id} -- so a retry after a
    failure never re-pays for information we already have.

    Confirmed via a live curl test against a real course id: GET
    /courses/{course_id} (not /courses/{course_id}/scorecard, despite what
    the docs implied) already returns the full tee_sets + holes payload
    directly on the bare object -- no wrapper, no separate scorecard call
    needed.

    A club can have more than one named course (e.g. two separate 18-hole
    layouts sharing one clubhouse) -- this used to just import
    club_courses[0] and stop there, which meant any additional course
    never got a row in the DB at all and could never show up in the Start
    New Round course list, for the lifetime of the app, since nothing else
    ever revisits a club once it has at least one cached course. Every
    course this club has now gets its own row the first time ANY course
    from this club is imported (see the loop below) -- for zero extra
    external API requests, since the full course id/name list is already
    sitting in the one /clubs response this was going to fetch anyway.
    Only the course actually being asked for gets its full tees/holes
    fetched right now; the others stay name-only until a player picks one,
    same lazy on-demand pattern this function already used for the
    single-course case.
    """
    external_club_id = details["external_club_id"]
    external_course_id = details.get("external_course_id")

    with _timed(f"select courses rows by external_club_id={external_club_id}", "database"):
        existing = (
            supabase
            .table("courses")
            .select("*")
            .eq("external_club_id", external_club_id)
            .execute()
        )
    existing_rows = existing.data or []

    # Make sure every course this club has is already a row before trying
    # to resolve which one the caller wants -- a requested external_course_id
    # might belong to a course that's never been seen before (e.g. the
    # second course of a two-course club), and this is what creates its
    # row. Costs one extra request (GET /clubs/{id}) the first time any
    # course from this club is touched; every row gets
    # club_courses_discovered=True afterward so this never re-spends a
    # request on the same club twice. In practice this has almost always
    # already run by now, from list_courses_for_club when the by-club
    # dropdown was populated -- this is just a safety net for callers that
    # skip straight to import (e.g. the re-import-from-search flow).
    if not any(row.get("club_courses_discovered") for row in existing_rows):
        try:
            existing_rows = _ensure_club_courses_discovered(
                details.get("club_name"), external_club_id, existing_rows,
            )
        except ExternalApiError:
            # Discovery failing (rate limit, API down) shouldn't block
            # resolving whatever course we already have cached -- fall
            # through to the existing-rows logic below with what we had.
            pass

    if external_course_id:
        matching_row = next(
            (row for row in existing_rows if row.get("external_course_id") == external_course_id),
            None,
        )
        if matching_row:
            cached = get_course(matching_row["id"])
            if cached and cached.get("tees"):
                return cached
            return _fetch_and_store_course_detail(matching_row, external_course_id)

    # No specific course requested -- fall back to whichever course this
    # club resolves to first (now guaranteed to exist, post-discovery,
    # for any club that has at least one real course).
    target_row = next((row for row in existing_rows if row.get("external_course_id")), None)
    if not target_row:
        raise ExternalApiError(
            404, f"No courses discovered for club {external_club_id}.",
        )

    cached = get_course(target_row["id"])
    if cached and cached.get("tees"):
        return cached
    return _fetch_and_store_course_detail(target_row, target_row["external_course_id"])


def ensure_scorecard(course_id: str) -> dict | None:
    """
    Given our internal course id (what the frontend actually has after
    someone picks a course from the local cache), guarantees a full
    scorecard is cached for it -- fetching from the live API on demand if
    we've only got the club-level name/county/postcode from the regions
    crawl so far. Cheap (just a DB read) if it's already been imported.
    """
    with _timed(f"select courses row by id={course_id}", "database"):
        course_response = (
            supabase.table("courses").select("*").eq("id", course_id).maybe_single().execute()
        )

    if course_response is None:
        return None

    course_row = course_response.data
    if not course_row:
        return None

    return import_course({
        "external_club_id": course_row["external_club_id"],
        # Passed through so import_course fetches THIS row's course
        # specifically -- without it, a club with more than one course
        # always resolved back to whichever course happened to be
        # imported first (see import_course's own docstring).
        "external_course_id": course_row.get("external_course_id"),
        "club_name": course_row["club_name"],
        "county": course_row.get("county"),
        "postcode": course_row.get("postcode"),
    })