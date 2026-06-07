from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class DrawingResponse(BaseModel):
    id: str
    project_id: str
    filename: str
    drawing_type: str  # floor_plan, fire_layout, architectural
    file_path: str
    mime_type: str
    file_size: int
    uploaded_at: datetime


class DrawingUploadResponse(BaseModel):
    success: bool
    drawing: DrawingResponse
    message: str
