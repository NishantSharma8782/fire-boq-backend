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


class BOQReport(BaseModel):
    id: str
    project_id: str
    sections: List[BOQSection]
    total_items: int
    generated_at: datetime
    notes: Optional[str] = ""


class BOQGenerateResponse(BaseModel):
    success: bool
    message: str
    boq: Optional[BOQReport] = None
