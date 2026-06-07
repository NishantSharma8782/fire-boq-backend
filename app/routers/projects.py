from fastapi import APIRouter, HTTPException, status
from bson import ObjectId
from datetime import datetime
from app.models.project import ProjectCreate, ProjectResponse, ProjectSummary
from app.db.database import get_collection

router = APIRouter(prefix="/projects", tags=["Projects"])


def _generate_project_id() -> str:
    """Generate sequential project ID like FIRE-2026-0001."""
    year = datetime.now().year
    col = get_collection("projects")
    # Synchronous count (motor doesn't need await for count in some versions)
    return f"FIRE-{year}-TEMP"  # Will be replaced by async version


async def _gen_project_id() -> str:
    year = datetime.now().year
    col = get_collection("projects")
    count = await col.count_documents({"project_id": {"$regex": f"^FIRE-{year}-"}})
    return f"FIRE-{year}-{count + 1:04d}"


def _serialize(doc: dict) -> dict:
    """Convert MongoDB doc to serializable dict."""
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(project: ProjectCreate):
    col = get_collection("projects")
    project_id = await _gen_project_id()
    now = datetime.utcnow()
    doc = {
        **project.dict(),
        "project_id": project_id,
        "status": "draft",
        "created_at": now,
        "updated_at": now,
    }
    result = await col.insert_one(doc)
    created = await col.find_one({"_id": result.inserted_id})
    return _serialize(created)


@router.get("/", response_model=list[ProjectSummary])
async def list_projects():
    col = get_collection("projects")
    drawings_col = get_collection("drawings")
    analyses_col = get_collection("analyses")
    boq_col = get_collection("boq_reports")

    projects = []
    async for doc in col.find().sort("created_at", -1):
        pid = doc.get("project_id", "")
        drawing_count = await drawings_col.count_documents({"project_id": pid})
        has_analysis = await analyses_col.count_documents({"project_id": pid}) > 0
        has_boq = await boq_col.count_documents({"project_id": pid}) > 0
        serialized = _serialize(doc)
        serialized["drawing_count"] = drawing_count
        serialized["has_analysis"] = has_analysis
        serialized["has_boq"] = has_boq
        projects.append(serialized)
    return projects


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    col = get_collection("projects")
    doc = await col.find_one({"project_id": project_id})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return _serialize(doc)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, project: ProjectCreate):
    col = get_collection("projects")
    doc = await col.find_one({"project_id": project_id})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    await col.update_one(
        {"project_id": project_id},
        {"$set": {**project.dict(), "updated_at": datetime.utcnow()}}
    )
    updated = await col.find_one({"project_id": project_id})
    return _serialize(updated)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str):
    col = get_collection("projects")
    doc = await col.find_one({"project_id": project_id})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    # Delete related data
    await get_collection("drawings").delete_many({"project_id": project_id})
    await get_collection("analyses").delete_many({"project_id": project_id})
    await get_collection("boq_reports").delete_many({"project_id": project_id})
    await col.delete_one({"project_id": project_id})
