from fastapi import APIRouter, HTTPException
from bson import ObjectId
from datetime import datetime
from app.models.analysis import (
    AnalysisResponse,
    AnalysisTriggerResponse,
    ManualBuildingInput,
)
from app.db.database import get_collection
from app.services import gemini_service
from app.services.boq_engine import calculate_fire_recommendations
from app.services.layout_engine import generate_layout

router = APIRouter(prefix="/analysis", tags=["Analysis"])


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


async def _save_analysis(
    project_id: str,
    building_data: dict,
    hazard_category: str,
    data_source: str,
    raw_analysis: str = "",
) -> dict:
    """
    Common helper: compute recommendations + layout, persist to DB,
    update project status, and return the serialised analysis doc.
    """
    analyses_col = get_collection("analyses")
    projects_col = get_collection("projects")

    recommendations = calculate_fire_recommendations(building_data, hazard_category)
    layout_data = generate_layout(building_data, recommendations)

    now = datetime.utcnow()
    doc = {
        "project_id": project_id,
        "building_data": building_data,
        "recommendations": recommendations,
        "layout_data": layout_data,
        "raw_analysis": raw_analysis,
        "data_source": data_source,
        "created_at": now,
    }

    await analyses_col.delete_many({"project_id": project_id})
    result = await analyses_col.insert_one(doc)
    created = await analyses_col.find_one({"_id": result.inserted_id})

    await projects_col.update_one(
        {"project_id": project_id},
        {"$set": {"status": "analyzed", "updated_at": now}},
    )

    return _serialize(created)


@router.post("/{project_id}/analyze", response_model=AnalysisTriggerResponse)
async def trigger_analysis(project_id: str):
    """
    Trigger Gemini AI analysis for a project's drawings.

    On AI failure the endpoint returns HTTP 200 with success=False and
    requires_manual=True — the frontend must show the manual entry form.
    It NEVER returns fake/default building data.
    """
    projects_col = get_collection("projects")
    drawings_col = get_collection("drawings")

    project = await projects_col.find_one({"project_id": project_id})
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    drawing = await drawings_col.find_one(
        {"project_id": project_id},
        sort=[("uploaded_at", -1)],
    )
    if not drawing:
        raise HTTPException(
            status_code=400,
            detail="No drawings found. Please upload a drawing first.",
        )

    hazard_category = project.get("hazard_category", "light")
    building_type = project.get("building_type", "office")

    # ── Run Gemini analysis ────────────────────────────────────────────────────
    analysis_result = await gemini_service.analyze_drawing(
        file_path=drawing["file_path"],
        building_type=building_type,
        hazard_category=hazard_category,
    )

    # ── AI failed — return structured error, DO NOT use fake data ─────────────
    if not analysis_result.get("success"):
        return AnalysisTriggerResponse(
            success=False,
            message="AI analysis failed. Please enter building measurements manually.",
            error_code=analysis_result.get("error_code", "UNKNOWN_ERROR"),
            error_message=analysis_result.get("error_message", "An unknown error occurred."),
            requires_manual=True,
        )

    # ── AI succeeded — persist and return ─────────────────────────────────────
    created = await _save_analysis(
        project_id=project_id,
        building_data=analysis_result["building_data"],
        hazard_category=hazard_category,
        data_source="ai",
        raw_analysis=analysis_result.get("raw", ""),
    )

    return AnalysisTriggerResponse(
        success=True,
        message="AI analysis completed successfully",
        analysis=created,
    )


@router.post("/{project_id}/manual", response_model=AnalysisTriggerResponse)
async def submit_manual_analysis(project_id: str, body: ManualBuildingInput):
    """
    Accept manually entered building measurements, skip Gemini entirely,
    and compute BOQ recommendations + layout from the provided data.
    """
    projects_col = get_collection("projects")

    project = await projects_col.find_one({"project_id": project_id})
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    hazard_category = project.get("hazard_category", "light")

    building_data = body.model_dump()

    created = await _save_analysis(
        project_id=project_id,
        building_data=building_data,
        hazard_category=hazard_category,
        data_source="manual",
        raw_analysis="Manually entered by user",
    )

    return AnalysisTriggerResponse(
        success=True,
        message="Manual building data saved successfully",
        analysis=created,
    )


@router.get("/{project_id}", response_model=AnalysisResponse)
async def get_analysis(project_id: str):
    """Get analysis results for a project."""
    analyses_col = get_collection("analyses")
    doc = await analyses_col.find_one({"project_id": project_id})
    if not doc:
        raise HTTPException(
            status_code=404,
            detail="No analysis found for this project. Run analysis first.",
        )
    return _serialize(doc)
