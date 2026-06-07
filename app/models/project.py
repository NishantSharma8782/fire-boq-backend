from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ProjectCreate(BaseModel):
    project_name: str
    client_name: str
    location: str
    building_type: str  # office, residential, industrial, hospital, school, warehouse, hotel, other
    hazard_category: str  # light, ordinary, high
    remarks: Optional[str] = ""


class ProjectResponse(BaseModel):
    id: str
    project_id: str
    project_name: str
    client_name: str
    location: str
    building_type: str
    hazard_category: str
    remarks: Optional[str] = ""
    status: str  # draft, drawing_uploaded, analyzed, boq_generated
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectSummary(BaseModel):
    id: str
    project_id: str
    project_name: str
    client_name: str
    location: str
    building_type: str
    hazard_category: str
    status: str
    created_at: datetime
    drawing_count: int = 0
    has_analysis: bool = False
    has_boq: bool = False
