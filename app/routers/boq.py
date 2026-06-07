from fastapi import APIRouter, HTTPException
from bson import ObjectId
from datetime import datetime
from app.models.boq import BOQReport, BOQGenerateResponse
from app.db.database import get_collection
from app.services.boq_engine import generate_boq

router = APIRouter(prefix="/boq", tags=["BOQ"])


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


@router.post("/{project_id}/generate", response_model=BOQGenerateResponse)
async def generate_project_boq(project_id: str):
    """Generate BOQ from analysis results."""
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

    # Generate BOQ
    boq_data = generate_boq(
        project=project,
        building_data=building_data,
        recommendations=recommendations,
        hazard_category=project.get("hazard_category", "light"),
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
        message="BOQ generated successfully",
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
