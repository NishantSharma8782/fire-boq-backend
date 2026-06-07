import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.db.database import connect_db, close_db
from app.routers import projects, drawings, analysis, boq, chat, export
from app.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_db()
    # Ensure upload directory exists
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    yield
    # Shutdown
    await close_db()


app = FastAPI(
    title="Fire BOQ Platform API",
    description="AI-powered Fire Bill of Quantities generation system",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow all origins for single-user MVP
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# Routers
app.include_router(projects.router, prefix="/api/v1")
app.include_router(drawings.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(boq.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(export.router, prefix="/api/v1")

# Serve uploaded files
upload_path = Path(settings.upload_dir)
upload_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(upload_path)), name="uploads")


@app.get("/")
async def root():
    return {
        "name": "Fire BOQ Platform API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}
