from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class ChatMessage(BaseModel):
    role: str  # user | assistant
    content: str
    timestamp: Optional[datetime] = None


class ChatRequest(BaseModel):
    project_id: str
    message: str
    history: Optional[List[ChatMessage]] = []


class ChatResponse(BaseModel):
    success: bool
    reply: str
    timestamp: datetime
