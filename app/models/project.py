from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


AI_MODELS = ["gemini", "openai", "groq", "claude"]
DEFAULT_AI_MODEL = "gemini"
FIRE_STANDARDS = ["NBC", "NFPA"]
DEFAULT_FIRE_STANDARD = "NBC"


class ProjectCreate(BaseModel):
    project_name: str
    client_name: str
    location: str
    building_type: str  # office, residential, industrial, hospital, school, warehouse, hotel, other
    hazard_category: str  # light, ordinary, high
    ai_model: str = DEFAULT_AI_MODEL  # gemini, openai, groq, claude
    fire_standard: str = DEFAULT_FIRE_STANDARD  # NBC, NFPA
    remarks: Optional[str] = ""


class ProjectResponse(BaseModel):
    id: str
    project_id: str
    project_name: str
    client_name: str
    location: str
    building_type: str
    hazard_category: str
    ai_model: str = DEFAULT_AI_MODEL
    fire_standard: str = DEFAULT_FIRE_STANDARD
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
    ai_model: str = DEFAULT_AI_MODEL
    fire_standard: str = DEFAULT_FIRE_STANDARD
    status: str
    created_at: datetime
    drawing_count: int = 0
    has_analysis: bool = False
    has_boq: bool = False
