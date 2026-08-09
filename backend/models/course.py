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
    """A course found via a live search of the UK Golf API — not yet cached."""
    external_club_id: str
    external_course_id: str
    club_name: str
    course_name: Optional[str] = None
    county: Optional[str] = None
    postcode: Optional[str] = None


class CourseImportRequest(BaseModel):
    external_club_id: str
    external_course_id: str
    club_name: str
    course_name: Optional[str] = None
    county: Optional[str] = None
    postcode: Optional[str] = None