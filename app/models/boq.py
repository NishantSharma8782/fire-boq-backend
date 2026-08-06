from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class BOQItem(BaseModel):
    sno: int
    item: str
    description: str
    unit: str
    quantity: float
    calculation_basis: str


class BOQSection(BaseModel):
    section_id: str  # A, B, C
    section_name: str  # Fire Hydrant System, Fire Sprinkler System, Fire Alarm System
    items: List[BOQItem]


class BOQGenerateRequest(BaseModel):
    standard: str = "NBC"             # "NBC" or "NFPA"
    boq_type: str = "manual"          # "manual" or "ai"
    ai_model: Optional[str] = None    # if None, uses the project's saved ai_model


class BOQUpdateRequest(BaseModel):
    sections: List[BOQSection]
    notes: Optional[str] = None



class BOQReport(BaseModel):
    id: str
    project_id: str
    sections: List[BOQSection]
    total_items: int
    generated_at: datetime
    notes: Optional[str] = ""
    standard: str = "NBC"
    boq_type: str = "manual"
    ai_model: str = ""


class BOQGenerateResponse(BaseModel):
    success: bool
    message: str
    boq: Optional[BOQReport] = None
