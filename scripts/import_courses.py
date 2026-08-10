# target path: scripts/import_courses.py
"""
One-off script to bulk-import UK golf CLUB names (not full scorecards) from
the UK Golf API, so the home-course search has a local name/county/postcode
directory to search against.

There's no free-text "search by club name" on this API, despite what their
own tutorial implies -- the real /clubs endpoint only filters by country,
county, postcode, type, rating, facilities, and green fees. The only way to
get the full club list is to walk every region and page through its clubs,
which is what this script does.

Full scorecards (tees/holes, per specific course) are still fetched later,
on demand, via import_course() in backend/services/courses.py, only when a
player actually needs one (e.g. logging a round) -- not needed just to
populate the home-course dropdown.

Run after applying courses_regions_migration.sql:

    uv run python scripts/import_courses.py

Confirmed from real runs: /regions returns a bare JSON array. Each
/regions/{id}/clubs page looks like:
    {"total": N, "page": 1, "per_page": 50, "total_pages": N, "clubs": [...]}
Pagination is a "page" number (1-indexed), not limit/offset. per_page
defaults to 20 but accepts up to 50 (a per_page=100 request got a 422:
"Input should be less than or equal to 50") -- we request 50 to minimise
total requests. We page using total_pages until exhausted, so regions with
more than 50 clubs aren't silently truncated.

Your RapidAPI plan is the free tier: 5 requests/min AND 200 requests/month
total. Even at per_page=50, walking all ~64 regions for ~2,668 clubs could
take 60-70+ requests -- a big chunk of the monthly budget -- before a
single scorecard is ever imported. To live with that:
  - This script hard-stops once it's made MAX_REQUESTS_THIS_RUN requests,
    rather than risking blowing the whole monthly quota in one go.
  - It skips regions that already have cached rows (cheap local check, no
    API cost), so re-running after a stop -- or next month once the quota
    resets -- picks up roughly where it left off instead of re-spending
    budget on regions already done.
"""
import time

import requests

from backend.database import supabase
from backend.services.courses import RAPIDAPI_BASE_URL, _rapidapi_headers

SECONDS_BETWEEN_REQUESTS = 13  # plan allows 5/min -- 12s minimum, padded for margin
RATE_LIMIT_BACKOFF_SECONDS = 60  # a per-minute limit needs the full window to clear
MAX_RATE_LIMIT_RETRIES = 5

# Leaves ~20 requests of this month's 200 in reserve for actual scorecard
# imports. Raise this next month (or after the quota resets) to keep going.
MAX_REQUESTS_THIS_RUN = 180

_request_count = 0


def _get(path: str, params: dict | None = None) -> dict | list:
    global _request_count

    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        response = requests.get(
            f"{RAPIDAPI_BASE_URL}{path}", params=params, headers=_rapidapi_headers()
        )
        _request_count += 1

        if response.status_code == 429:
            print(f"  Rate limited on {path}, waiting {RATE_LIMIT_BACKOFF_SECONDS}s...")
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
            continue

        if response.status_code != 200:
            raise RuntimeError(f"{path} returned {response.status_code}: {response.text}")

        time.sleep(SECONDS_BETWEEN_REQUESTS)
        return response.json()

    raise RuntimeError(f"{path} still rate-limited after {MAX_RATE_LIMIT_RETRIES} retries")


def _unwrap_list(payload, *keys):
    """Some endpoints on this API return a bare JSON array (confirmed for
    /regions); others wrap results under a key (confirmed "clubs" for
    /regions/{id}/clubs). Handle both rather than assuming one envelope
    shape everywhere."""
    if isinstance(payload, list):
        return payload

    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value

    print(f"  Unexpected response shape, raw payload: {payload}")
    return []


def _region_already_cached(region_name: str) -> bool:
    response = (
        supabase.table("courses").select("id").eq("county", region_name).limit(1).execute()
    )
    return bool(response.data)


def import_all_clubs():
    regions_payload = _get("/regions")
    print(f"Raw /regions response (truncated): {str(regions_payload)[:500]}\n")

    regions = _unwrap_list(regions_payload, "data", "regions", "items")
    if not regions:
        print("No regions found -- check the raw payload above before continuing.")
        return

    print(f"Found {len(regions)} regions.\n")

    total_imported = 0

    for region_index, region in enumerate(regions):
        if _request_count >= MAX_REQUESTS_THIS_RUN:
            print(f"\nStopping early: hit the {MAX_REQUESTS_THIS_RUN}-request budget for this "
                  f"run at region {region_index + 1}/{len(regions)}. Re-run this script (next "
                  f"month, once quota resets, if needed) to pick up the remaining regions -- "
                  f"already-cached ones will be skipped automatically.")
            break

        region_id = region.get("id")
        region_name = region.get("name", str(region_id))

        if region_id is None:
            print(f"Skipping region with no id: {region}")
            continue

        if _region_already_cached(region_name):
            print(f"[{region_index + 1}/{len(regions)}] {region_name}: already cached, skipping")
            continue

        page = 1
        total_pages = 1
        region_imported = 0

        while page <= total_pages:
            clubs_payload = _get(
                f"/regions/{region_id}/clubs",
                # Confirmed via a 422 validation error: per_page must be
                # <= 50. 50 is the real ceiling, not the 20 we saw by
                # default -- this roughly halves the total request count.
                params={"page": page, "per_page": 50},
            )

            if region_index == 0 and page == 1:
                print(f"Raw first clubs page for '{region_name}' (truncated): "
                      f"{str(clubs_payload)[:500]}\n")

            clubs = _unwrap_list(clubs_payload, "data", "clubs", "items")

            if isinstance(clubs_payload, dict):
                total_pages = clubs_payload.get("total_pages", 1) or 1

            if not clubs:
                break

            rows = [
                {
                    "external_club_id": str(club["id"]),
                    "external_course_id": None,
                    "club_name": club["name"],
                    "course_name": None,
                    "county": club.get("county") or region_name,
                    "postcode": club.get("postcode"),
                }
                for club in clubs
            ]

            supabase.table("courses").upsert(rows, on_conflict="external_club_id").execute()

            region_imported += len(rows)
            page += 1

        total_imported += region_imported
        print(f"[{region_index + 1}/{len(regions)}] {region_name}: {region_imported} clubs "
              f"(requests so far: {_request_count})")

    print(f"\nDone this run. {total_imported} club rows upserted. "
          f"{_request_count} API requests used.")


if __name__ == "__main__":
    import_all_clubs()