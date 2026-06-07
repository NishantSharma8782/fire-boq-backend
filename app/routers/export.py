from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from app.db.database import get_collection
from app.services.export_service import generate_pdf, generate_excel, generate_csv
import io

router = APIRouter(prefix="/export", tags=["Export"])


async def _get_export_data(project_id: str):
    projects_col = get_collection("projects")
    analyses_col = get_collection("analyses")
    boq_col = get_collection("boq_reports")

    project = await projects_col.find_one({"project_id": project_id})
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    analysis = await analyses_col.find_one({"project_id": project_id})
    boq = await boq_col.find_one({"project_id": project_id})

    if not boq:
        raise HTTPException(
            status_code=400,
            detail="No BOQ generated yet. Please generate BOQ first."
        )

    # Convert ObjectId to str
    for doc in [project, analysis, boq]:
        if doc and "_id" in doc:
            doc["id"] = str(doc.pop("_id"))

    return project, analysis or {}, boq


@router.get("/{project_id}/pdf")
async def export_pdf(project_id: str):
    project, analysis, boq = await _get_export_data(project_id)

    pdf_bytes = generate_pdf(
        project=project,
        building_data=analysis.get("building_data", {}),
        recommendations=analysis.get("recommendations", {}),
        boq_sections=boq.get("sections", []),
    )

    filename = f"FireBOQ_{project.get('project_id', project_id)}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/excel")
async def export_excel(project_id: str):
    project, analysis, boq = await _get_export_data(project_id)

    excel_bytes = generate_excel(
        project=project,
        building_data=analysis.get("building_data", {}),
        recommendations=analysis.get("recommendations", {}),
        boq_sections=boq.get("sections", []),
    )

    filename = f"FireBOQ_{project.get('project_id', project_id)}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/csv")
async def export_csv(project_id: str):
    project, analysis, boq = await _get_export_data(project_id)

    csv_str = generate_csv(boq_sections=boq.get("sections", []))
    filename = f"FireBOQ_{project.get('project_id', project_id)}.csv"

    return Response(
        content=csv_str.encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
