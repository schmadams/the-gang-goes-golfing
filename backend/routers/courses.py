# target path: backend/routers/courses.py (full replacement)
from fastapi import APIRouter, HTTPException, Query, status

from backend.models.course import (
    CourseDetailResponse,
    CourseImportRequest,
    CourseResponse,
    ExternalCourseCandidate,
)
from backend.services.courses import (
    ExternalApiError,
    ensure_scorecard,
    get_course,
    import_course,
    search_external_clubs,
    search_local_courses,
)

router = APIRouter(
    prefix="/courses",
    tags=["courses"],
)


@router.get("/", response_model=list[CourseResponse])
def search_courses_route(search: str = Query(default="")):
    return search_local_courses(search)


@router.get("/external-search", response_model=list[ExternalCourseCandidate])
def external_search_route(query: str = Query(...)):
    try:
        return search_external_clubs(query)
    except ExternalApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"UK Golf API returned {exc.status_code}: {exc.body}",
        )


@router.post("/import", response_model=CourseDetailResponse, status_code=status.HTTP_201_CREATED)
def import_course_route(payload: CourseImportRequest):
    try:
        course = import_course(payload.model_dump())
    except ExternalApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"UK Golf API returned {exc.status_code}: {exc.body}",
        )

    if not course:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Couldn't import course"
        )

    return course


@router.post("/{course_id}/scorecard", response_model=CourseDetailResponse)
def ensure_scorecard_route(course_id: str):
    try:
        course = ensure_scorecard(course_id)
    except ExternalApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"UK Golf API returned {exc.status_code}: {exc.body}",
        )

    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found"
        )

    return course


@router.get("/{course_id}", response_model=CourseDetailResponse)
def get_course_route(course_id: str):
    course = get_course(course_id)

    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found"
        )

    return course