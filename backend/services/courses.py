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

    for tee_set in course_detail.get("tee_sets", []):
        # "name" is always null on this API -- the tee is actually
        # identified by "colour" (e.g. "black", "red"). Title-case it so it
        # reads naturally wherever we display tee.name (e.g. "Black tees").
        tee_name = tee_set.get("name") or (tee_set.get("colour") or "Unknown").title()

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

    NOTE: if a club has multiple named courses (e.g. Wentworth's West/East),
    this currently just imports the first one listed. Picking a specific
    course is a future improvement, not needed yet.
    """
    external_club_id = details["external_club_id"]

    with _timed(f"select courses row by external_club_id={external_club_id}", "database"):
        existing = (
            supabase
            .table("courses")
            .select("*")
            .eq("external_club_id", external_club_id)
            .maybe_single()
            .execute()
        )
    existing_row = existing.data if existing is not None else None

    if existing_row and existing_row.get("external_course_id"):
        cached = get_course(existing_row["id"])
        if cached and cached.get("tees"):
            return cached
        # We already know which course this club maps to (saved from a
        # previous attempt) -- go straight to course detail instead of
        # re-spending a request on /clubs to re-derive the same course id.
        return _fetch_and_store_course_detail(existing_row, existing_row["external_course_id"])

    with _timed(f"GET /clubs/{external_club_id}", "external API"):
        club_response = requests.get(
            f"{RAPIDAPI_BASE_URL}/clubs/{external_club_id}",
            headers=_rapidapi_headers(),
        )
    if club_response.status_code != 200:
        raise ExternalApiError(club_response.status_code, club_response.text)

    club = _unwrap_object(club_response.json(), "id", "name", "courses")

    # Save what this call taught us immediately -- club_name/county/postcode
    # here may be more accurate than what the regions crawl cached -- so
    # this request isn't wasted even if the next step (course lookup) fails.
    existing_row = _upsert_course_row(existing_row, {
        "external_club_id": external_club_id,
        "club_name": details.get("club_name") or club.get("name"),
        "county": details.get("county") or club.get("county"),
        "postcode": details.get("postcode") or club.get("postcode"),
    })

    club_courses = club.get("courses") or []
    if not club_courses:
        # Include the raw body, not just our parsed (possibly empty) dict --
        # if unwrapping still guessed wrong, this is what tells us why. The
        # club-level info above is already saved regardless of this error.
        raise ExternalApiError(
            404,
            f"No courses listed for club {external_club_id}. "
            f"Parsed: {club} | Raw: {club_response.text[:500]}",
        )

    course_id = club_courses[0]["id"]
    course_name = club_courses[0].get("name")

    # Save the course id/name before spending the next request on full
    # detail -- if that call fails, a retry will skip straight to fetching
    # detail (one request) instead of re-calling /clubs (two requests).
    existing_row = _upsert_course_row(existing_row, {
        "external_course_id": course_id,
        "course_name": course_name,
    })

    return _fetch_and_store_course_detail(existing_row, course_id)


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
        "club_name": course_row["club_name"],
        "county": course_row.get("county"),
        "postcode": course_row.get("postcode"),
    })