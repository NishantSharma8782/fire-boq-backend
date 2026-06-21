from fastapi import APIRouter, HTTPException, Query
from datetime import datetime
from app.models.chat import ChatRequest, ChatResponse, ChatMessage
from app.db.database import get_collection
from app.services import ai_service

router = APIRouter(prefix="/chat", tags=["AI Assistant"])


# ── GET: Load saved chat history for a project (paginated) ───────────────────
@router.get("/{project_id}/history")
async def get_chat_history(
    project_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
):
    """Return paginated chat messages for a project, oldest-first."""
    chats_col = get_collection("chat_messages")

    total = await chats_col.count_documents({"project_id": project_id})
    skip = (page - 1) * page_size

    cursor = chats_col.find(
        {"project_id": project_id},
        {"_id": 0, "project_id": 0},
    ).sort("timestamp", 1).skip(skip).limit(page_size)

    messages = await cursor.to_list(length=page_size)

    return {
        "project_id": project_id,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "messages": messages,
    }


# ── POST: Send a message and get AI reply (also saves both to DB) ─────────────
@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    projects_col  = get_collection("projects")
    analyses_col  = get_collection("analyses")
    boq_col       = get_collection("boq_reports")
    chats_col     = get_collection("chat_messages")

    project = await projects_col.find_one({"project_id": request.project_id})
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {request.project_id} not found")

    analysis = await analyses_col.find_one({"project_id": request.project_id})
    boq      = await boq_col.find_one({"project_id": request.project_id})
    ai_model = project.get("ai_model", "gemini")

    # Build context
    project_context = {
        "project": {
            "name":           project.get("project_name"),
            "id":             project.get("project_id"),
            "client":         project.get("client_name"),
            "building_type":  project.get("building_type"),
            "hazard_category":project.get("hazard_category"),
            "location":       project.get("location"),
            "ai_model":       ai_model,
        },
        "building_analysis":    analysis.get("building_data")     if analysis else None,
        "fire_recommendations": analysis.get("recommendations")   if analysis else None,
        "boq_sections": [
            {"section": s.get("section_name"), "item_count": len(s.get("items", []))}
            for s in (boq.get("sections", []) if boq else [])
        ],
    }

    # Use the last 10 messages from DB as history context (most recent)
    recent_cursor = chats_col.find(
        {"project_id": request.project_id},
        {"_id": 0, "project_id": 0},
    ).sort("timestamp", -1).limit(10)
    recent_docs = await recent_cursor.to_list(length=10)
    recent_docs.reverse()  # oldest first for context

    # Also merge any extra history the client sent (for immediate context)
    client_history = [msg.dict() for msg in (request.history or [])]
    history = recent_docs if recent_docs else client_history

    now = datetime.utcnow()

    # Save user message first
    await chats_col.insert_one({
        "project_id": request.project_id,
        "role":       "user",
        "content":    request.message,
        "timestamp":  now,
    })

    reply = await ai_service.chat_with_context(
        message=request.message,
        project_context=project_context,
        history=history,
        ai_model=ai_model,
    )

    reply_time = datetime.utcnow()

    # Save AI reply
    await chats_col.insert_one({
        "project_id": request.project_id,
        "role":       "assistant",
        "content":    reply,
        "timestamp":  reply_time,
    })

    return ChatResponse(
        success=True,
        reply=reply,
        timestamp=reply_time,
    )
