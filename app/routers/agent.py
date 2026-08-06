"""
Agent Router — Exposes:
  POST /agent/{project_id}/execute       — run AI instruction on BOQ/project
  GET  /agent/{project_id}/history       — list past agent actions (undo-able)
  POST /agent/{project_id}/undo/{hid}    — restore BOQ/project to pre-action state
"""
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.db.database import get_collection
from app.services import agent_service
from app.services.ai_boq_service import generate_ai_boq

router = APIRouter(prefix="/agent", tags=["BOQ Agent"])

HISTORY_LIMIT = 30  # max entries per project


# ── Request / Response models ──────────────────────────────────────────────────

class AgentRequest(BaseModel):
    instruction: str
    context: Optional[str] = None
    confirmed: bool = False   # True = user explicitly confirmed a suspicious instruction


class AgentResponse(BaseModel):
    success: bool
    summary: str
    applied_changes: List[str]
    intent: str
    boq_updated: bool
    history_id: Optional[str] = None
    needs_confirmation: bool = False
    confirmation_message: Optional[str] = None
    confirmation_type: Optional[str] = None


class HistoryEntry(BaseModel):
    history_id: str
    instruction: str
    intent: str
    summary: str
    applied_changes: List[str]
    timestamp: datetime
    can_undo: bool = True


# ── Helpers ────────────────────────────────────────────────────────────────────

def _strip_id(doc: dict) -> dict:
    """Remove MongoDB _id from document."""
    doc.pop("_id", None)
    return doc


async def _save_history(
    history_col,
    project_id: str,
    instruction: str,
    intent: str,
    summary: str,
    applied_changes: list,
    boq_snapshot: Optional[dict],
    project_snapshot: Optional[dict],
) -> str:
    """Save a history entry and return its history_id."""
    hid = str(uuid.uuid4())
    await history_col.insert_one({
        "history_id": hid,
        "project_id": project_id,
        "instruction": instruction,
        "intent": intent,
        "summary": summary,
        "applied_changes": applied_changes,
        "boq_snapshot": boq_snapshot,
        "project_snapshot": project_snapshot,
        "timestamp": datetime.utcnow(),
    })
    # Keep only latest HISTORY_LIMIT entries per project
    all_entries = await history_col.find(
        {"project_id": project_id},
        {"_id": 1}
    ).sort("timestamp", -1).to_list(length=None)
    if len(all_entries) > HISTORY_LIMIT:
        old_ids = [e["_id"] for e in all_entries[HISTORY_LIMIT:]]
        await history_col.delete_many({"_id": {"$in": old_ids}})
    return hid


# ── POST /execute ──────────────────────────────────────────────────────────────

@router.post("/{project_id}/execute", response_model=AgentResponse)
async def execute_agent(project_id: str, req: AgentRequest):
    projects_col  = get_collection("projects")
    analyses_col  = get_collection("analyses")
    boq_col       = get_collection("boq_reports")
    history_col   = get_collection("agent_history")

    # Validate project
    project = await projects_col.find_one({"project_id": project_id})
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    # Get current BOQ
    current_boq = await boq_col.find_one({"project_id": project_id})
    if not current_boq:
        raise HTTPException(
            status_code=400,
            detail="No BOQ found. Please generate a BOQ first."
        )

    # Execute AI agent
    ai_model = project.get("ai_model", "groq")
    result = await agent_service.execute_agent_instruction(
        instruction=req.instruction,
        project_id=project_id,
        project=project,
        current_boq=current_boq,
        ai_model=ai_model,
        confirmed=req.confirmed,
    )

    # ── Confirmation required — return without executing ────────────────────────
    if result.get("needs_confirmation"):
        return AgentResponse(
            success=False,
            summary=result["summary"],
            applied_changes=[],
            intent=result.get("intent", "unknown"),
            boq_updated=False,
            needs_confirmation=True,
            confirmation_message=result.get("confirmation_message"),
            confirmation_type=result.get("confirmation_type"),
        )

    if not result["success"]:
        return AgentResponse(
            success=False,
            summary=result["summary"],
            applied_changes=result["applied_changes"],
            intent=result.get("intent", "unknown"),
            boq_updated=False,
        )

    intent   = result.get("intent", "custom_update")
    summary  = result["summary"]
    changes  = result["applied_changes"]
    now      = datetime.utcnow()

    # ── Project field update ───────────────────────────────────────────────────
    project_updates = result.get("project_updates") or {}
    if intent == "update_project" and project_updates:
        # Snapshot old project fields for undo
        old_project_fields = {k: project.get(k) for k in project_updates}
        hid = await _save_history(
            history_col, project_id, req.instruction, intent, summary, changes,
            boq_snapshot=None,
            project_snapshot=old_project_fields,
        )
        await projects_col.update_one(
            {"project_id": project_id},
            {"$set": {**project_updates, "updated_at": now}},
        )
        return AgentResponse(
            success=True, summary=summary, applied_changes=changes,
            intent=intent, boq_updated=False, history_id=hid,
        )

    # ── BOQ regeneration ───────────────────────────────────────────────────────
    if result.get("requires_regeneration"):
        analysis = await analyses_col.find_one({"project_id": project_id})
        if not analysis:
            raise HTTPException(status_code=400, detail="No analysis found. Cannot regenerate BOQ.")

        new_standard = result.get("new_standard") or current_boq.get("standard", "NBC")
        boq_data = await generate_ai_boq(
            building_data=analysis.get("building_data", {}),
            recommendations=analysis.get("recommendations", {}),
            project=project,
            standard=new_standard.upper(),
            ai_model=ai_model,
        )

        # Snapshot old BOQ
        old_boq = _strip_id(dict(current_boq))
        hid = await _save_history(
            history_col, project_id, req.instruction, intent, summary, changes,
            boq_snapshot=old_boq, project_snapshot=None,
        )

        new_doc = {
            "project_id": project_id,
            **boq_data,
            "generated_at": now,
            "agent_modified_at": now,
            "agent_last_instruction": req.instruction,
        }
        await boq_col.delete_many({"project_id": project_id})
        await boq_col.insert_one(new_doc)

        return AgentResponse(
            success=True, summary=summary, applied_changes=changes,
            intent=intent, boq_updated=True, history_id=hid,
        )

    # ── Targeted BOQ modifications ─────────────────────────────────────────────
    updated_sections = result["updated_sections"]
    total_items = sum(len(s.get("items", [])) for s in updated_sections)

    # Snapshot old BOQ for undo
    old_boq = _strip_id(dict(current_boq))
    hid = await _save_history(
        history_col, project_id, req.instruction, intent, summary, changes,
        boq_snapshot=old_boq, project_snapshot=None,
    )

    await boq_col.update_one(
        {"project_id": project_id},
        {"$set": {
            "sections": updated_sections,
            "total_items": total_items,
            "agent_modified_at": now,
            "agent_last_instruction": req.instruction,
        }}
    )
    await projects_col.update_one(
        {"project_id": project_id},
        {"$set": {"updated_at": now}}
    )

    return AgentResponse(
        success=True, summary=summary, applied_changes=changes,
        intent=intent, boq_updated=True, history_id=hid,
    )


# ── GET /history ───────────────────────────────────────────────────────────────

@router.get("/{project_id}/history")
async def get_agent_history(project_id: str):
    """Return the last N agent actions for this project (newest first)."""
    history_col = get_collection("agent_history")
    cursor = history_col.find(
        {"project_id": project_id},
        {"_id": 0, "boq_snapshot": 0, "project_snapshot": 0},
    ).sort("timestamp", -1).limit(HISTORY_LIMIT)
    entries = await cursor.to_list(length=HISTORY_LIMIT)
    return {"project_id": project_id, "history": entries}


# ── POST /undo/{history_id} ────────────────────────────────────────────────────

@router.post("/{project_id}/undo/{history_id}")
async def undo_agent_action(project_id: str, history_id: str):
    """Restore the BOQ/project to the state it was in before the given action."""
    history_col  = get_collection("agent_history")
    boq_col      = get_collection("boq_reports")
    projects_col = get_collection("projects")

    entry = await history_col.find_one({
        "history_id": history_id,
        "project_id": project_id,
    })
    if not entry:
        raise HTTPException(status_code=404, detail="History entry not found.")

    now = datetime.utcnow()
    restored = []

    # Restore BOQ snapshot
    boq_snapshot = entry.get("boq_snapshot")
    if boq_snapshot:
        boq_snapshot["agent_modified_at"] = now
        boq_snapshot["agent_last_instruction"] = f"[UNDO] {entry.get('instruction', '')}"
        await boq_col.delete_many({"project_id": project_id})
        await boq_col.insert_one(boq_snapshot)
        restored.append("BOQ restored")

    # Restore project snapshot
    project_snapshot = entry.get("project_snapshot")
    if project_snapshot:
        await projects_col.update_one(
            {"project_id": project_id},
            {"$set": {**project_snapshot, "updated_at": now}},
        )
        restored.append("Project fields restored")

    if not restored:
        return {"success": False, "summary": "Nothing to restore for this action."}

    # Remove this history entry (and all newer ones? No — just mark it)
    await history_col.delete_one({"history_id": history_id})

    return {
        "success": True,
        "summary": f"Undid: {entry.get('summary', 'action')}",
        "restored": restored,
    }



