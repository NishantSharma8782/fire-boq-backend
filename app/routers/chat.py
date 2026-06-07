from fastapi import APIRouter, HTTPException
from datetime import datetime
from app.models.chat import ChatRequest, ChatResponse
from app.db.database import get_collection
from app.services import gemini_service

router = APIRouter(prefix="/chat", tags=["AI Assistant"])


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    projects_col = get_collection("projects")
    analyses_col = get_collection("analyses")
    boq_col = get_collection("boq_reports")

    project = await projects_col.find_one({"project_id": request.project_id})
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {request.project_id} not found")

    analysis = await analyses_col.find_one({"project_id": request.project_id})
    boq = await boq_col.find_one({"project_id": request.project_id})

    # Build context for Gemini
    project_context = {
        "project": {
            "name": project.get("project_name"),
            "id": project.get("project_id"),
            "client": project.get("client_name"),
            "building_type": project.get("building_type"),
            "hazard_category": project.get("hazard_category"),
            "location": project.get("location"),
        },
        "building_analysis": analysis.get("building_data") if analysis else None,
        "fire_recommendations": analysis.get("recommendations") if analysis else None,
        "boq_sections": [
            {"section": s.get("section_name"), "item_count": len(s.get("items", []))}
            for s in (boq.get("sections", []) if boq else [])
        ],
    }

    history = [msg.dict() for msg in (request.history or [])]

    reply = await gemini_service.chat_with_context(
        message=request.message,
        project_context=project_context,
        history=history,
    )

    return ChatResponse(
        success=True,
        reply=reply,
        timestamp=datetime.utcnow(),
    )
