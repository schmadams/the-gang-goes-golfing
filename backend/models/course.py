# target path: backend/models/course.py (full replacement)
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class HoleResponse(BaseModel):
    id: UUID
    hole_number: int
    par: int
    yardage: Optional[int] = None
    stroke_index: Optional[int] = None


class TeeResponse(BaseModel):
    id: UUID
    name: str
    gender: Optional[str] = None
    par: Optional[int] = None
    course_rating: Optional[float] = None
    slope_rating: Optional[int] = None
    holes: list[HoleResponse] = []


class CourseResponse(BaseModel):
    id: UUID
    club_name: str
    course_name: Optional[str] = None
    county: Optional[str] = None
    postcode: Optional[str] = None
    designed_by: Optional[str] = None
    year_opened: Optional[int] = None
    created_at: Optional[datetime] = None


class CourseDetailResponse(CourseResponse):
    tees: list[TeeResponse] = []


class ExternalCourseCandidate(BaseModel):
    """A club found via a live search of the UK Golf API — not yet cached.
    Kept for a future name/location-filtered search; the API has no
    free-text name search today (see search_external_clubs)."""
    external_club_id: str
    club_name: str
    county: Optional[str] = None
    postcode: Optional[str] = None


class CourseImportRequest(BaseModel):
    """
    What's needed to pull a club's full scorecard on demand. Only the club
    id is required -- import_course() looks up that club's course(s) and
    scorecard itself, since the bulk regions crawl only ever caches
    club-level info (name/county/postcode), not a specific course id.
    """
    external_club_id: str
    club_name: str
    county: Optional[str] = None
    postcode: Optional[str] = None