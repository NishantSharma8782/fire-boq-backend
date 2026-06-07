import os
import shutil
import aiofiles
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, status
from fastapi.responses import FileResponse
from bson import ObjectId
from datetime import datetime
from pathlib import Path
from app.models.drawing import DrawingResponse, DrawingUploadResponse
from app.db.database import get_collection
from app.config import get_settings

router = APIRouter(prefix="/drawings", tags=["Drawings"])
settings = get_settings()

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "application/pdf": ".pdf",
    "image/tiff": ".tiff",
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


@router.post("/upload", response_model=DrawingUploadResponse)
async def upload_drawing(
    project_id: str = Form(...),
    drawing_type: str = Form(...),  # floor_plan, fire_layout, architectural
    file: UploadFile = File(...),
):
    # Validate project exists
    projects_col = get_collection("projects")
    project = await projects_col.find_one({"project_id": project_id})
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    # Validate file type
    content_type = file.content_type or ""
    if content_type not in ALLOWED_TYPES and not file.filename.lower().endswith((".jpg", ".jpeg", ".png", ".pdf")):
        raise HTTPException(
            status_code=400,
            detail=f"File type not supported. Allowed: PDF, PNG, JPG, JPEG"
        )

    # Create project upload directory
    upload_dir = Path(settings.upload_dir) / project_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = Path(file.filename).suffix.lower() or ".bin"
    safe_name = f"{drawing_type}_{timestamp}{ext}"
    file_path = upload_dir / safe_name

    # Save file
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(contents)

    # Save to DB
    col = get_collection("drawings")
    doc = {
        "project_id": project_id,
        "filename": file.filename,
        "drawing_type": drawing_type,
        "file_path": str(file_path),
        "mime_type": content_type or "application/octet-stream",
        "file_size": len(contents),
        "uploaded_at": datetime.utcnow(),
    }
    result = await col.insert_one(doc)
    created = await col.find_one({"_id": result.inserted_id})

    # Update project status
    await projects_col.update_one(
        {"project_id": project_id},
        {"$set": {"status": "drawing_uploaded", "updated_at": datetime.utcnow()}}
    )

    return DrawingUploadResponse(
        success=True,
        drawing=_serialize(created),
        message=f"Drawing '{file.filename}' uploaded successfully"
    )


@router.get("/{project_id}", response_model=list[DrawingResponse])
async def list_drawings(project_id: str):
    col = get_collection("drawings")
    drawings = []
    async for doc in col.find({"project_id": project_id}).sort("uploaded_at", -1):
        drawings.append(_serialize(doc))
    return drawings


@router.get("/file/{drawing_id}")
async def get_drawing_file(drawing_id: str):
    col = get_collection("drawings")
    try:
        doc = await col.find_one({"_id": ObjectId(drawing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid drawing ID")

    if not doc:
        raise HTTPException(status_code=404, detail="Drawing not found")

    file_path = Path(doc["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Drawing file not found on disk")

    return FileResponse(
        path=str(file_path),
        media_type=doc.get("mime_type", "application/octet-stream"),
        filename=doc.get("filename", "drawing"),
    )


@router.delete("/{drawing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_drawing(drawing_id: str):
    col = get_collection("drawings")
    try:
        doc = await col.find_one({"_id": ObjectId(drawing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid drawing ID")

    if not doc:
        raise HTTPException(status_code=404, detail="Drawing not found")

    # Delete file from disk
    file_path = Path(doc["file_path"])
    if file_path.exists():
        file_path.unlink()

    await col.delete_one({"_id": ObjectId(drawing_id)})
