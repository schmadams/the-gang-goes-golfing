# target path: backend/services/course.py (full replacement)
import os

import requests

from backend.database import supabase

RAPIDAPI_BASE_URL = "https://uk-golf-api.vercel.app"
RAPIDAPI_HOST = "uk-golf-course-data-api.p.rapidapi.com"


def _rapidapi_headers() -> dict:
    api_key = os.environ.get("UK_GOLF_API_KEY")

    if not api_key:
        raise EnvironmentError(
            "Missing UK_GOLF_API_KEY. Get a free key at "
            "https://rapidapi.com/raznut303/api/uk-golf-course-data-api "
            "and add it to your .env file."
        )

    return {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": RAPIDAPI_HOST}


def search_local_courses(query: str, limit: int = 20) -> list[dict]:
    """
    Search courses we've already cached, for the type-ahead dropdown.
    Free — this never touches the external API or its monthly quota.
    """
    if not query:
        return []

    escaped = query.replace(",", " ").replace("%", "")

    response = (
        supabase
        .table("courses")
        .select("*")
        .or_(f"club_name.ilike.%{escaped}%,course_name.ilike.%{escaped}%")
        .order("club_name")
        .limit(limit)
        .execute()
    )

    return response.data


def search_external_clubs(query: str) -> list[dict]:
    """
    Search the live UK Golf API for clubs matching `query`. Read-only against
    our own database — this just gives the user candidates to pick from when
    their course isn't in our local cache yet. Spends one of our monthly
    API requests.
    """
    response = requests.get(
        f"{RAPIDAPI_BASE_URL}/clubs",
        params={"search": query},
        headers=_rapidapi_headers(),
    )
    response.raise_for_status()
    clubs = response.json().get("data", [])

    candidates = []
    for club in clubs:
        club_courses = club.get("courses") or [{"id": club["id"], "name": None}]
        for course in club_courses:
            candidates.append({
                "external_club_id": club["id"],
                "external_course_id": course["id"],
                "club_name": club["name"],
                "course_name": course.get("name"),
                "county": club.get("county"),
                "postcode": club.get("postcode"),
            })

    return candidates


def get_course(course_id: str) -> dict | None:
    course_response = (
        supabase.table("courses").select("*").eq("id", course_id).maybe_single().execute()
    )

    if course_response is None:
        return None

    course = course_response.data
    if not course:
        return None

    tees_response = (
        supabase.table("course_tees").select("*").eq("course_id", course_id).execute()
    )
    tees = tees_response.data or []

    for tee in tees:
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


def import_course(details: dict) -> dict | None:
    """
    Fetch one course's full scorecard from the live API and cache it locally
    (courses / course_tees / course_holes) so we never have to call the
    external API for this course again. This is the only place in the app
    that spends one of our monthly API requests on a specific course, and
    only ever does so once per course.
    """
    external_course_id = details["external_course_id"]

    existing = (
        supabase
        .table("courses")
        .select("id")
        .eq("external_course_id", external_course_id)
        .maybe_single()
        .execute()
    )
    if existing is not None and existing.data:
        return get_course(existing.data["id"])

    response = requests.get(
        f"{RAPIDAPI_BASE_URL}/courses/{external_course_id}/scorecard",
        headers=_rapidapi_headers(),
    )
    response.raise_for_status()
    scorecard = response.json().get("data", {})

    course_row = (
        supabase
        .table("courses")
        .insert({
            "external_club_id": details["external_club_id"],
            "external_course_id": external_course_id,
            "club_name": details["club_name"],
            "course_name": details.get("course_name") or scorecard.get("name"),
            "county": details.get("county"),
            "postcode": details.get("postcode"),
            "designed_by": scorecard.get("designed_by"),
            "year_opened": scorecard.get("year_opened"),
        })
        .execute()
        .data[0]
    )

    for tee_set in scorecard.get("tee_sets", []):
        tee_row = (
            supabase
            .table("course_tees")
            .insert({
                "course_id": course_row["id"],
                "name": tee_set["name"],
                "gender": tee_set.get("gender"),
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
            supabase.table("course_holes").insert(holes).execute()

    return get_course(course_row["id"])