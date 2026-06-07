"""
BOQ Engine - Calculates fire system quantities based on NBC 2016 / IS standards.
All calculations are deterministic and based on building analysis data.
"""
from typing import Dict, Any, List
from app.models.boq import BOQItem, BOQSection, BOQReport
from app.models.analysis import FireRecommendations
from datetime import datetime


def calculate_fire_recommendations(building_data: dict, hazard_category: str) -> dict:
    """
    Calculate fire equipment quantities from building data.
    Based on NBC 2016 Part 4, IS 2189, IS 15105.
    """
    area = float(building_data.get("estimated_area", 100))
    rooms = int(building_data.get("rooms", 1))
    floors = int(building_data.get("floors", 1))
    corridors = int(building_data.get("corridors", 0))
    stairs = int(building_data.get("stairs", 0))
    ceiling_height = float(building_data.get("ceiling_height", 3.0))
    building_type = building_data.get("building_type", "office").lower()

    hazard = hazard_category.lower()

    # ── SMOKE DETECTORS ─────────────────────────────────────────────────────────
    # NBC 2016: 1 detector per 60 sqm (light), 40 sqm (ordinary), 30 sqm (high)
    coverage_map = {"light": 60, "ordinary": 40, "high": 30}
    detector_coverage = coverage_map.get(hazard, 60)
    smoke_detectors = max(2, int(area / detector_coverage) + corridors + stairs)

    # ── HEAT DETECTORS ───────────────────────────────────────────────────────────
    # Used in kitchens, parking, electrical rooms (~15% of total detectors)
    heat_detectors = max(1, int(smoke_detectors * 0.15))
    if building_type in ["industrial", "warehouse"]:
        heat_detectors = max(2, int(smoke_detectors * 0.3))

    # ── MCP (Manual Call Points) ─────────────────────────────────────────────────
    # IS 2189: 1 MCP per floor, near staircase, max 30m travel distance
    mcp = max(floors, max(2, int(area / 500) * floors))

    # ── HOOTERS / SOUNDERS ───────────────────────────────────────────────────────
    # 1 per 1000 sqm, min 2 per floor, ensure audibility 75dB
    hooters = max(2 * floors, int(area / 1000) * floors + floors)

    # ── FIRE EXTINGUISHERS ───────────────────────────────────────────────────────
    # IS 2190: 1 per 100 sqm (light), 1 per 75 sqm (ordinary), 1 per 50 sqm (high)
    ext_coverage = {"light": 100, "ordinary": 75, "high": 50}
    fire_extinguishers = max(2, int(area / ext_coverage.get(hazard, 100)))

    # ── HYDRANTS ────────────────────────────────────────────────────────────────
    # IS 3844: 1 per 500 sqm, max 30m between hydrants
    hydrants = max(1, int(area / 500) + floors - 1)
    if building_type in ["industrial", "warehouse"]:
        hydrants = max(2, int(area / 300))

    # ── SPRINKLERS ───────────────────────────────────────────────────────────────
    # IS 15105: 1 per 12 sqm (light), 1 per 9 sqm (ordinary), 1 per 7 sqm (high)
    sprinkler_coverage = {"light": 12, "ordinary": 9, "high": 7}
    sprinklers = max(4, int(area / sprinkler_coverage.get(hazard, 12)))

    # ── HOSE REELS ───────────────────────────────────────────────────────────────
    # 1 per landing, near hydrant
    hose_reels = max(1, hydrants)

    # ── FIRE ALARM PANEL ─────────────────────────────────────────────────────────
    fire_alarm_panel = 1
    if area > 5000:
        fire_alarm_panel = 2

    return {
        "smoke_detectors": smoke_detectors,
        "heat_detectors": heat_detectors,
        "mcp": mcp,
        "hooters": hooters,
        "fire_extinguishers": fire_extinguishers,
        "hydrants": hydrants,
        "sprinklers": sprinklers,
        "fire_alarm_panel": fire_alarm_panel,
        "hose_reels": hose_reels,
        "placement_strategy": f"Grid-based placement across {floors} floor(s) with {smoke_detectors} detection points. "
                               f"Detectors spaced at {detector_coverage}m² coverage per point.",
    }


def generate_boq(project: dict, building_data: dict, recommendations: dict, hazard_category: str) -> dict:
    """
    Generate full BOQ from building data and recommendations.
    """
    area = float(building_data.get("estimated_area", 100))
    floors = int(building_data.get("floors", 1))

    smoke_det = int(recommendations.get("smoke_detectors", 2))
    heat_det = int(recommendations.get("heat_detectors", 1))
    mcp_count = int(recommendations.get("mcp", 2))
    hooter_count = int(recommendations.get("hooters", 2))
    hydrant_count = int(recommendations.get("hydrants", 1))
    sprinkler_count = int(recommendations.get("sprinklers", 4))
    hose_reel_count = int(recommendations.get("hose_reels", 1))
    panel_count = int(recommendations.get("fire_alarm_panel", 1))

    # ── SECTION A: FIRE HYDRANT SYSTEM ──────────────────────────────────────────
    # Pipe length estimate: perimeter-based (2 * sqrt(area) * 4 sides * floors)
    import math
    side = math.sqrt(area)
    perimeter = 4 * side
    hydrant_pipe_length = round(perimeter * floors * 1.2, 1)  # 20% extra for bends
    branch_pipe_length = round(hydrant_count * 5, 1)

    section_a = BOQSection(
        section_id="A",
        section_name="Fire Hydrant System",
        items=[
            BOQItem(sno=1, item="GI Pipe 80mm NB",
                    description="Galvanized Iron Pipe 80mm NB, Class Medium, IS 1239, for main fire hydrant ring main",
                    unit="Rmt", quantity=hydrant_pipe_length,
                    calculation_basis=f"Perimeter {round(perimeter)}m × {floors} floors × 1.2 factor"),
            BOQItem(sno=2, item="GI Pipe 65mm NB",
                    description="Galvanized Iron Pipe 65mm NB, Class Medium, IS 1239, for branch lines",
                    unit="Rmt", quantity=branch_pipe_length,
                    calculation_basis=f"{hydrant_count} hydrants × 5m branch each"),
            BOQItem(sno=3, item="Hydrant Valve 65mm",
                    description="ISI marked Sluice Valve / Landing Valve 65mm, IS 908, with hose coupling",
                    unit="Nos", quantity=float(hydrant_count),
                    calculation_basis=f"1 per 500 sqm, area={round(area)}sqm, {floors} floors"),
            BOQItem(sno=4, item="Landing Valve 63mm",
                    description="External Landing Valve 63mm dia with blank cap and chain, IS 5290",
                    unit="Nos", quantity=float(max(1, floors)),
                    calculation_basis=f"1 per floor, {floors} floors"),
            BOQItem(sno=5, item="Hose Reel Drum",
                    description="Hose Reel 30m length, 25mm dia rubber hose, IS 884, wall mounted with nozzle",
                    unit="Nos", quantity=float(hose_reel_count),
                    calculation_basis=f"1 per hydrant location, {hose_reel_count} locations"),
            BOQItem(sno=6, item="Butterfly Valve 80mm",
                    description="PN 16 Butterfly Valve 80mm with lever operator, IS 13095",
                    unit="Nos", quantity=float(max(2, hydrant_count)),
                    calculation_basis="Isolation valves at main + branches"),
            BOQItem(sno=7, item="Pressure Gauge",
                    description="0-16 bar pressure gauge 100mm dial, glycerin filled, IS 3624",
                    unit="Nos", quantity=float(max(2, hydrant_count)),
                    calculation_basis="At each hydrant riser"),
            BOQItem(sno=8, item="GI Pipe Fittings 80mm",
                    description="GI elbows, tees, reducers, unions for 80mm pipe, IS 1239",
                    unit="Lot", quantity=1.0,
                    calculation_basis="Lump sum for pipe network fittings"),
        ],
    )

    # ── SECTION B: FIRE SPRINKLER SYSTEM ────────────────────────────────────────
    sprinkler_pipe = round(area / 10 * floors, 1)  # ~100mm per sqm of area
    distribution_pipe = round(sprinkler_count * 1.5, 1)

    section_b = BOQSection(
        section_id="B",
        section_name="Fire Sprinkler System",
        items=[
            BOQItem(sno=1, item="Sprinkler Head",
                    description="Glass Bulb Sprinkler Head 15mm, 68°C (red), K-factor 80, IS 15105, pendent type",
                    unit="Nos", quantity=float(sprinkler_count),
                    calculation_basis=f"Coverage: {round(area/sprinkler_count,1)} sqm/head, total area {round(area)} sqm"),
            BOQItem(sno=2, item="GI Pipe 25mm NB",
                    description="Galvanized Iron Pipe 25mm NB, Class Heavy, IS 1239, for sprinkler distribution",
                    unit="Rmt", quantity=distribution_pipe,
                    calculation_basis=f"{sprinkler_count} heads × 1.5m avg spacing"),
            BOQItem(sno=3, item="GI Pipe 50mm NB",
                    description="Galvanized Iron Pipe 50mm NB, Class Heavy, IS 1239, for sprinkler branch mains",
                    unit="Rmt", quantity=float(round(sprinkler_pipe * 0.3, 1)),
                    calculation_basis="30% of total pipe length for branch mains"),
            BOQItem(sno=4, item="GI Pipe 80mm NB",
                    description="Galvanized Iron Pipe 80mm NB, Class Heavy, IS 1239, for sprinkler main riser",
                    unit="Rmt", quantity=float(round(floors * 4, 1)),
                    calculation_basis=f"{floors} floors × 4m riser height"),
            BOQItem(sno=5, item="Flow Switch",
                    description="Paddle type flow switch 80mm, NFPA 13 compliant, with tamper switch",
                    unit="Nos", quantity=float(floors),
                    calculation_basis=f"1 per floor, {floors} floors"),
            BOQItem(sno=6, item="Alarm Check Valve 80mm",
                    description="Wet alarm check valve 80mm with retard chamber and water motor alarm",
                    unit="Nos", quantity=1.0,
                    calculation_basis="1 per sprinkler system"),
            BOQItem(sno=7, item="Control Valve (OS&Y) 80mm",
                    description="Outside Screw & Yoke gate valve 80mm with supervisory switch, IS 780",
                    unit="Nos", quantity=float(max(1, floors)),
                    calculation_basis=f"Zone control valve per floor, {floors} zones"),
            BOQItem(sno=8, item="Inspector Test Valve",
                    description="Inspector test and drain valve 25mm with sight glass, per zone",
                    unit="Nos", quantity=float(floors),
                    calculation_basis=f"1 per zone, {floors} zones"),
        ],
    )

    # ── SECTION C: FIRE ALARM SYSTEM ─────────────────────────────────────────────
    # Cable length: avg 2m per detector + backbone
    cable_length = round((smoke_det + heat_det + mcp_count + hooter_count) * 2.5 + area * 0.05, 1)
    conduit_length = round(cable_length * 0.8, 1)

    section_c = BOQSection(
        section_id="C",
        section_name="Fire Alarm System",
        items=[
            BOQItem(sno=1, item="Smoke Detector",
                    description="Photoelectric smoke detector, IS 2189, addressable, with base, 9V LED indicator",
                    unit="Nos", quantity=float(smoke_det),
                    calculation_basis=f"1 per {60 if hazard_category=='light' else 40} sqm, area {round(area)} sqm + {mcp_count} corridors"),
            BOQItem(sno=2, item="Heat Detector",
                    description="Rate-of-rise + fixed temperature heat detector 57°C/83°C, IS 2189, addressable",
                    unit="Nos", quantity=float(heat_det),
                    calculation_basis="15% of total detectors for kitchen/electrical areas"),
            BOQItem(sno=3, item="Manual Call Point (MCP)",
                    description="Break glass MCP, IS 2189, addressable, red with protective cover",
                    unit="Nos", quantity=float(mcp_count),
                    calculation_basis=f"Min 1 per floor near staircase, {floors} floors, max 30m travel"),
            BOQItem(sno=4, item="Hooter / Sounder",
                    description="Electronic hooter 105dB, red, IS 2189, flush/surface mount",
                    unit="Nos", quantity=float(hooter_count),
                    calculation_basis=f"Min 2 per floor, {floors} floors, area {round(area)} sqm"),
            BOQItem(sno=5, item="Fire Alarm Control Panel",
                    description=f"Addressable FACP {smoke_det + heat_det + mcp_count + hooter_count}-point capacity, IS 2189, with battery backup 48hr",
                    unit="Nos", quantity=float(panel_count),
                    calculation_basis=f"Total addressable points: {smoke_det + heat_det + mcp_count + hooter_count}"),
            BOQItem(sno=6, item="Fire Resistant Cable 1.5sqmm",
                    description="2-core 1.5sqmm fire resistant cable (FR), IS 7098, for detector loop",
                    unit="Rmt", quantity=cable_length,
                    calculation_basis=f"({smoke_det}+{heat_det}+{mcp_count}+{hooter_count}) devices × 2.5m avg + backbone"),
            BOQItem(sno=7, item="GI Conduit 20mm",
                    description="GI conduit 20mm dia, IS 9537, for cable protection, with bends and accessories",
                    unit="Rmt", quantity=conduit_length,
                    calculation_basis=f"80% of total cable length {cable_length}m"),
            BOQItem(sno=8, item="End-of-Line Resistor",
                    description="End of line resistor kit for supervised loop circuit, IS 2189",
                    unit="Nos", quantity=float(max(2, mcp_count)),
                    calculation_basis="1 per detection zone loop"),
            BOQItem(sno=9, item="12V 7Ah Battery",
                    description="Sealed maintenance-free battery 12V 7Ah, for FACP standby backup",
                    unit="Nos", quantity=float(panel_count * 2),
                    calculation_basis=f"{panel_count} panel(s) × 2 batteries each"),
            BOQItem(sno=10, item="Junction Box",
                    description="GI junction box 4×4 inch with 20mm knockouts for cable terminations",
                    unit="Nos", quantity=float(max(4, smoke_det // 3)),
                    calculation_basis="1 per 3 detectors for loop distribution"),
        ],
    )

    total_items = len(section_a.items) + len(section_b.items) + len(section_c.items)

    boq = {
        "sections": [section_a.dict(), section_b.dict(), section_c.dict()],
        "total_items": total_items,
        "notes": (
            f"BOQ generated as per NBC 2016 Part 4, IS 2189:2008, IS 15105, IS 3844. "
            f"Quantities are based on AI analysis of building drawings. "
            f"Final quantities to be verified on site. "
            f"Building area: {round(area)} sqm, Floors: {floors}, Hazard: {hazard_category.upper()}."
        ),
    }
    return boq
