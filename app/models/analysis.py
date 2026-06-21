from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class BuildingData(BaseModel):
    building_type: Optional[str] = "unknown"
    rooms: Optional[int] = 0
    estimated_area: Optional[float] = 0.0
    floors: Optional[int] = 1
    corridors: Optional[int] = 0
    stairs: Optional[int] = 0
    entrances: Optional[int] = 1
    exits: Optional[int] = 1
    open_areas: Optional[int] = 0
    ceiling_height: Optional[float] = 3.0
    description: Optional[str] = ""


class ManualBuildingInput(BaseModel):
    """Validated manual entry of building measurements."""
    building_type: str = Field(default="office", description="Type of building")
    estimated_area: float = Field(..., gt=0, le=1000000, description="Total floor area in sqm")
    rooms: int = Field(..., ge=0, le=10000, description="Number of rooms/spaces")
    floors: int = Field(..., ge=1, le=200, description="Number of floors")
    corridors: int = Field(default=0, ge=0, le=1000, description="Number of corridors")
    stairs: int = Field(default=0, ge=0, le=500, description="Number of staircases")
    entrances: int = Field(default=1, ge=0, le=500, description="Number of entrances")
    exits: int = Field(default=1, ge=0, le=500, description="Number of exits/emergency exits")
    open_areas: int = Field(default=0, ge=0, le=500, description="Number of open areas/lobbies")
    ceiling_height: float = Field(default=3.0, ge=2.0, le=50.0, description="Ceiling height in meters")
    description: str = Field(default="", max_length=2000, description="Optional description")


class FireRecommendations(BaseModel):
    smoke_detectors: int = 0
    heat_detectors: int = 0
    mcp: int = 0
    hooters: int = 0
    fire_extinguishers: int = 0
    hydrants: int = 0
    sprinklers: int = 0
    fire_alarm_panel: int = 1
    hose_reels: int = 0
    placement_strategy: Optional[str] = ""


class LayoutCoordinate(BaseModel):
    x: float
    y: float
    label: Optional[str] = ""


class LayoutData(BaseModel):
    canvas_width: float = 800
    canvas_height: float = 600
    scale: float = 1.0
    building_outline: List[Dict[str, float]] = []
    smoke_detectors: List[LayoutCoordinate] = []
    heat_detectors: List[LayoutCoordinate] = []
    mcp: List[LayoutCoordinate] = []
    hooters: List[LayoutCoordinate] = []
    sprinklers: List[LayoutCoordinate] = []
    hydrants: List[LayoutCoordinate] = []
    fire_extinguishers: List[LayoutCoordinate] = []


class AnalysisResponse(BaseModel):
    id: str
    project_id: str
    building_data: BuildingData
    recommendations: FireRecommendations
    layout_data: LayoutData
    raw_analysis: Optional[str] = ""
    data_source: Optional[str] = "ai"
    created_at: datetime


class AnalysisTriggerResponse(BaseModel):
    success: bool
    message: str
    analysis: Optional[AnalysisResponse] = None
    # Fields populated on failure (when requires_manual=True)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    requires_manual: Optional[bool] = False
