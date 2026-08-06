from fastapi import APIRouter, HTTPException
from bson import ObjectId
from datetime import datetime
from app.models.boq import BOQReport, BOQGenerateResponse, BOQGenerateRequest, BOQUpdateRequest
from app.db.database import get_collection
from app.services.boq_engine import generate_boq
from app.services.ai_boq_service import generate_ai_boq

router = APIRouter(prefix="/boq", tags=["BOQ"])


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


@router.post("/{project_id}/generate", response_model=BOQGenerateResponse)
async def generate_project_boq(project_id: str, req: BOQGenerateRequest = BOQGenerateRequest()):
    """Generate BOQ from analysis results. Supports NBC/NFPA standards and manual/AI BOQ types."""
    projects_col = get_collection("projects")
    analyses_col = get_collection("analyses")
    boq_col = get_collection("boq_reports")

    project = await projects_col.find_one({"project_id": project_id})
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    analysis = await analyses_col.find_one({"project_id": project_id})
    if not analysis:
        raise HTTPException(
            status_code=400,
            detail="No analysis found. Please run AI analysis first."
        )

    building_data = analysis.get("building_data", {})
    recommendations = analysis.get("recommendations", {})

    standard = req.standard.upper() if req.standard else "NBC"
    boq_type = req.boq_type.lower() if req.boq_type else "manual"
    # Use project's ai_model as default — allow request to override
    project_ai_model = project.get("ai_model", "gemini")
    ai_model = req.ai_model.lower() if req.ai_model else project_ai_model


    # Route to AI or manual engine
    if boq_type == "ai":
        boq_data = await generate_ai_boq(
            building_data=building_data,
            recommendations=recommendations,
            project=project,
            standard=standard,
            ai_model=ai_model,
        )
    else:
        boq_data = generate_boq(
            project=project,
            building_data=building_data,
            recommendations=recommendations,
            hazard_category=project.get("hazard_category", "light"),
            standard=standard,
        )

    now = datetime.utcnow()
    doc = {
        "project_id": project_id,
        **boq_data,
        "generated_at": now,
    }

    # Replace existing BOQ
    await boq_col.delete_many({"project_id": project_id})
    result = await boq_col.insert_one(doc)
    created = await boq_col.find_one({"_id": result.inserted_id})

    # Update project status
    await projects_col.update_one(
        {"project_id": project_id},
        {"$set": {"status": "boq_generated", "updated_at": now}}
    )

    return BOQGenerateResponse(
        success=True,
        message=f"BOQ generated successfully ({standard} standard, {boq_type} mode)",
        boq=_serialize(created),
    )


@router.get("/{project_id}", response_model=BOQReport)
async def get_boq(project_id: str):
    boq_col = get_collection("boq_reports")
    doc = await boq_col.find_one({"project_id": project_id})
    if not doc:
        raise HTTPException(
            status_code=404,
            detail="No BOQ found. Please generate BOQ first."
        )
    return _serialize(doc)


@router.put("/{project_id}", response_model=BOQGenerateResponse)
async def update_boq(project_id: str, req: BOQUpdateRequest):
    """Update sections and items of an existing BOQ report."""
    boq_col = get_collection("boq_reports")
    doc = await boq_col.find_one({"project_id": project_id})
    if not doc:
        raise HTTPException(
            status_code=404,
            detail="No BOQ found to update. Please generate BOQ first."
        )

    sections_dict = [sec.dict() for sec in req.sections]
    total_items = sum(len(sec["items"]) for sec in sections_dict)

    update_fields = {
        "sections": sections_dict,
        "total_items": total_items,
        "updated_at": datetime.utcnow(),
    }
    if req.notes is not None:
        update_fields["notes"] = req.notes

    await boq_col.update_one(
        {"project_id": project_id},
        {"$set": update_fields}
    )

    updated_doc = await boq_col.find_one({"project_id": project_id})
    return BOQGenerateResponse(
        success=True,
        message="BOQ updated successfully",
        boq=_serialize(updated_doc),
    )

