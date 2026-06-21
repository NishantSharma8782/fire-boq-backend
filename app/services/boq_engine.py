"""
BOQ Engine - Calculates fire system quantities based on NBC 2016 / IS standards
or NFPA 72 / NFPA 13 / NFPA 10 standards.
All calculations are deterministic and based on building analysis data.
"""
from typing import Dict, Any, List
from app.models.boq import BOQItem, BOQSection, BOQReport
from app.models.analysis import FireRecommendations
from datetime import datetime
import math


# ── Coverage tables ────────────────────────────────────────────────────────────

NBC_COVERAGE = {
    "smoke_detector": {"light": 60, "ordinary": 40, "high": 30},        # sqm per detector
    "sprinkler":      {"light": 12, "ordinary": 9,  "high": 7},          # sqm per head
    "extinguisher":   {"light": 100, "ordinary": 75, "high": 50},        # sqm per unit
}

NFPA_COVERAGE = {
    # NFPA 72: max 9.1m spacing radius → π × 9.1² ≈ 83.6 sqm coverage
    "smoke_detector": {"light": 83.6, "ordinary": 46.5, "high": 28.0},
    # NFPA 13: Light Hazard 18.6 sqm, Ordinary 12.1 sqm, Extra 9.3 sqm
    "sprinkler":      {"light": 18.6, "ordinary": 12.1, "high": 9.3},
    # NFPA 10: Light 278 sqm, Ordinary 139 sqm, Extra Hazard 93 sqm
    "extinguisher":   {"light": 278, "ordinary": 139, "high": 93},
}


def _coverage(standard: str, item: str, hazard: str) -> float:
    table = NFPA_COVERAGE if standard == "NFPA" else NBC_COVERAGE
    return table.get(item, NBC_COVERAGE[item]).get(hazard, list(table[item].values())[0])


def calculate_fire_recommendations(building_data: dict, hazard_category: str, standard: str = "NBC") -> dict:
    """
    Calculate fire equipment quantities from building data.
    NBC: Based on NBC 2016 Part 4, IS 2189, IS 15105.
    NFPA: Based on NFPA 72 (alarm), NFPA 13 (sprinklers), NFPA 10 (extinguishers).
    """
    area = float(building_data.get("estimated_area", 100))
    rooms = int(building_data.get("rooms", 1))
    floors = int(building_data.get("floors", 1))
    corridors = int(building_data.get("corridors", 0))
    stairs = int(building_data.get("stairs", 0))
    ceiling_height = float(building_data.get("ceiling_height", 3.0))
    building_type = building_data.get("building_type", "office").lower()

    hazard = hazard_category.lower()
    std = standard.upper()

    # ── SMOKE DETECTORS ─────────────────────────────────────────────────────────
    detector_coverage = _coverage(std, "smoke_detector", hazard)
    smoke_detectors = max(2, int(area / detector_coverage) + corridors + stairs)

    # ── HEAT DETECTORS ───────────────────────────────────────────────────────────
    heat_detectors = max(1, int(smoke_detectors * 0.15))
    if building_type in ["industrial", "warehouse"]:
        heat_detectors = max(2, int(smoke_detectors * 0.3))

    # ── MCP (Manual Call Points) ─────────────────────────────────────────────────
    # NBC: IS 2189 — 1 MCP per floor near staircase, max 30m travel
    # NFPA: NFPA 72 — pull stations within 60m travel distance, ≥1 per floor exit
    if std == "NFPA":
        mcp = max(floors, max(2, int(area / 600) * floors))
    else:
        mcp = max(floors, max(2, int(area / 500) * floors))

    # ── HOOTERS / SOUNDERS ───────────────────────────────────────────────────────
    # NFPA 72: 65dB at 3m distance; NBC: 75dB audibility
    hooters = max(2 * floors, int(area / 1000) * floors + floors)

    # ── FIRE EXTINGUISHERS ───────────────────────────────────────────────────────
    ext_coverage = _coverage(std, "extinguisher", hazard)
    fire_extinguishers = max(2, int(area / ext_coverage))

    # ── HYDRANTS ────────────────────────────────────────────────────────────────
    # NBC: IS 3844 — 1 per 500 sqm; NFPA: NFPA 14 — 1 per 465 sqm
    hydrant_spacing = 465 if std == "NFPA" else 500
    hydrants = max(1, int(area / hydrant_spacing) + floors - 1)
    if building_type in ["industrial", "warehouse"]:
        hydrants = max(2, int(area / 300))

    # ── SPRINKLERS ───────────────────────────────────────────────────────────────
    sprinkler_cov = _coverage(std, "sprinkler", hazard)
    sprinklers = max(4, int(area / sprinkler_cov))

    # ── HOSE REELS ───────────────────────────────────────────────────────────────
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
        "placement_strategy": (
            f"Grid-based placement across {floors} floor(s) with {smoke_detectors} detection points. "
            f"Detectors spaced at {detector_coverage:.0f}m² coverage per point ({std} standard)."
        ),
    }


def generate_boq(
    project: dict,
    building_data: dict,
    recommendations: dict,
    hazard_category: str,
    standard: str = "NBC",
) -> dict:
    """
    Generate full BOQ from building data and recommendations.
    Supports NBC 2016 / IS standards and NFPA standards.
    """
    area = float(building_data.get("estimated_area", 100))
    floors = int(building_data.get("floors", 1))
    std = standard.upper()

    smoke_det = int(recommendations.get("smoke_detectors", 2))
    heat_det = int(recommendations.get("heat_detectors", 1))
    mcp_count = int(recommendations.get("mcp", 2))
    hooter_count = int(recommendations.get("hooters", 2))
    hydrant_count = int(recommendations.get("hydrants", 1))
    sprinkler_count = int(recommendations.get("sprinklers", 4))
    hose_reel_count = int(recommendations.get("hose_reels", 1))
    panel_count = int(recommendations.get("fire_alarm_panel", 1))

    # ── SECTION A: FIRE HYDRANT SYSTEM ──────────────────────────────────────────
    side = math.sqrt(area)
    perimeter = 4 * side
    hydrant_pipe_length = round(perimeter * floors * 1.2, 1)
    branch_pipe_length = round(hydrant_count * 5, 1)

    if std == "NFPA":
        # NFPA 14 — standpipe system, 65mm NB wet standpipe
        pipe_spec_main = "Schedule 40 Steel Pipe 80mm NB, ASTM A53, for wet standpipe main"
        pipe_spec_branch = "Schedule 40 Steel Pipe 65mm NB, ASTM A53, for branch lines"
        valve_spec = "UL listed 65mm angle valve, NFPA 14 compliant, with 2.5\" hose coupling"
        landing_spec = "2.5\" Cabinet hose valve, UL listed, NFPA 14"
        hose_spec = "30m length, 38mm rubber hose, UL listed nozzle, NFPA 14"
        std_ref_a = "NFPA 14"
    else:
        pipe_spec_main = "Galvanized Iron Pipe 80mm NB, Class Medium, IS 1239, for main fire hydrant ring main"
        pipe_spec_branch = "Galvanized Iron Pipe 65mm NB, Class Medium, IS 1239, for branch lines"
        valve_spec = "ISI marked Sluice Valve / Landing Valve 65mm, IS 908, with hose coupling"
        landing_spec = "External Landing Valve 63mm dia with blank cap and chain, IS 5290"
        hose_spec = "Hose Reel 30m length, 25mm dia rubber hose, IS 884, wall mounted with nozzle"
        std_ref_a = "IS 3844 / NBC 2016"

    section_a = BOQSection(
        section_id="A",
        section_name="Fire Hydrant System",
        items=[
            BOQItem(sno=1, item="GI Pipe 80mm NB",
                    description=pipe_spec_main,
                    unit="Rmt", quantity=hydrant_pipe_length,
                    calculation_basis=f"Perimeter {round(perimeter)}m × {floors} floors × 1.2 factor ({std_ref_a})"),
            BOQItem(sno=2, item="GI Pipe 65mm NB",
                    description=pipe_spec_branch,
                    unit="Rmt", quantity=branch_pipe_length,
                    calculation_basis=f"{hydrant_count} hydrants × 5m branch each"),
            BOQItem(sno=3, item="Hydrant Valve 65mm",
                    description=valve_spec,
                    unit="Nos", quantity=float(hydrant_count),
                    calculation_basis=f"1 per {'465' if std=='NFPA' else '500'} sqm, area={round(area)}sqm, {floors} floors ({std_ref_a})"),
            BOQItem(sno=4, item="Landing Valve 63mm",
                    description=landing_spec,
                    unit="Nos", quantity=float(max(1, floors)),
                    calculation_basis=f"1 per floor, {floors} floors"),
            BOQItem(sno=5, item="Hose Reel Drum",
                    description=hose_spec,
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
    sprinkler_pipe = round(area / 10 * floors, 1)
    distribution_pipe = round(sprinkler_count * 1.5, 1)

    if std == "NFPA":
        sprinkler_spec = "Glass Bulb Sprinkler Head 15mm, 74°C (green), K-factor 80, NFPA 13, pendent type"
        flow_sw_spec = "Paddle type flow switch 80mm, UL listed, NFPA 13 with tamper switch"
        alarm_valve_spec = "Wet alarm check valve 80mm UL listed with retard chamber (NFPA 13)"
        ctrl_valve_spec = "OS&Y gate valve 80mm, UL listed, with supervisory switch (NFPA 13)"
        test_valve_spec = "Inspector test and drain valve 25mm with sight glass, NFPA 13 per zone"
        std_ref_b = "NFPA 13"
    else:
        sprinkler_spec = "Glass Bulb Sprinkler Head 15mm, 68°C (red), K-factor 80, IS 15105, pendent type"
        flow_sw_spec = "Paddle type flow switch 80mm, NFPA 13 compliant, with tamper switch"
        alarm_valve_spec = "Wet alarm check valve 80mm with retard chamber and water motor alarm"
        ctrl_valve_spec = "Outside Screw & Yoke gate valve 80mm with supervisory switch, IS 780"
        test_valve_spec = "Inspector test and drain valve 25mm with sight glass, per zone"
        std_ref_b = "IS 15105 / NBC 2016"

    section_b = BOQSection(
        section_id="B",
        section_name="Fire Sprinkler System",
        items=[
            BOQItem(sno=1, item="Sprinkler Head",
                    description=sprinkler_spec,
                    unit="Nos", quantity=float(sprinkler_count),
                    calculation_basis=f"Coverage: {round(area/sprinkler_count,1)} sqm/head, total area {round(area)} sqm ({std_ref_b})"),
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
                    description=flow_sw_spec,
                    unit="Nos", quantity=float(floors),
                    calculation_basis=f"1 per floor, {floors} floors"),
            BOQItem(sno=6, item="Alarm Check Valve 80mm",
                    description=alarm_valve_spec,
                    unit="Nos", quantity=1.0,
                    calculation_basis="1 per sprinkler system"),
            BOQItem(sno=7, item="Control Valve (OS&Y) 80mm",
                    description=ctrl_valve_spec,
                    unit="Nos", quantity=float(max(1, floors)),
                    calculation_basis=f"Zone control valve per floor, {floors} zones"),
            BOQItem(sno=8, item="Inspector Test Valve",
                    description=test_valve_spec,
                    unit="Nos", quantity=float(floors),
                    calculation_basis=f"1 per zone, {floors} zones"),
        ],
    )

    # ── SECTION C: FIRE ALARM SYSTEM ─────────────────────────────────────────────
    cable_length = round((smoke_det + heat_det + mcp_count + hooter_count) * 2.5 + area * 0.05, 1)
    conduit_length = round(cable_length * 0.8, 1)

    if std == "NFPA":
        smoke_spec = "Photoelectric smoke detector, NFPA 72, UL listed, addressable, with base, LED indicator"
        heat_spec = "Rate-of-rise + fixed temperature heat detector 135°F/190°F, NFPA 72, UL listed, addressable"
        mcp_spec = "Pull station MCP, NFPA 72, UL listed, addressable, red with tamper-resistant cover"
        hooter_spec = "Electronic notification appliance 110dB, UL listed, NFPA 72, flush/surface mount"
        panel_spec = f"Addressable FACP {smoke_det + heat_det + mcp_count + hooter_count}-point capacity, UL listed, NFPA 72, with 24hr battery backup"
        std_ref_c = "NFPA 72"
        cov_text = f"NFPA 72 coverage: 83.6 sqm/detector, area {round(area)} sqm"
    else:
        smoke_spec = "Photoelectric smoke detector, IS 2189, addressable, with base, 9V LED indicator"
        heat_spec = "Rate-of-rise + fixed temperature heat detector 57°C/83°C, IS 2189, addressable"
        mcp_spec = "Break glass MCP, IS 2189, addressable, red with protective cover"
        hooter_spec = "Electronic hooter 105dB, red, IS 2189, flush/surface mount"
        panel_spec = f"Addressable FACP {smoke_det + heat_det + mcp_count + hooter_count}-point capacity, IS 2189, with battery backup 48hr"
        std_ref_c = "IS 2189 / NBC 2016"
        _corridor_count = int(building_data.get('corridors', 0))
        cov_text = f"1 per {60 if hazard_category == 'light' else 40} sqm, area {round(area)} sqm + {_corridor_count} corridors"

    section_c = BOQSection(
        section_id="C",
        section_name="Fire Alarm System",
        items=[
            BOQItem(sno=1, item="Smoke Detector",
                    description=smoke_spec,
                    unit="Nos", quantity=float(smoke_det),
                    calculation_basis=cov_text),
            BOQItem(sno=2, item="Heat Detector",
                    description=heat_spec,
                    unit="Nos", quantity=float(heat_det),
                    calculation_basis="15% of total detectors for kitchen/electrical areas"),
            BOQItem(sno=3, item="Manual Call Point (MCP)",
                    description=mcp_spec,
                    unit="Nos", quantity=float(mcp_count),
                    calculation_basis=f"Min 1 per floor near staircase, {floors} floors, max {'60m' if std == 'NFPA' else '30m'} travel ({std_ref_c})"),
            BOQItem(sno=4, item="Hooter / Sounder",
                    description=hooter_spec,
                    unit="Nos", quantity=float(hooter_count),
                    calculation_basis=f"Min 2 per floor, {floors} floors, area {round(area)} sqm"),
            BOQItem(sno=5, item="Fire Alarm Control Panel",
                    description=panel_spec,
                    unit="Nos", quantity=float(panel_count),
                    calculation_basis=f"Total addressable points: {smoke_det + heat_det + mcp_count + hooter_count} ({std_ref_c})"),
            BOQItem(sno=6, item="Fire Resistant Cable 1.5sqmm",
                    description="2-core 1.5sqmm fire resistant cable (FR), IS 7098, for detector loop",
                    unit="Rmt", quantity=cable_length,
                    calculation_basis=f"({smoke_det}+{heat_det}+{mcp_count}+{hooter_count}) devices × 2.5m avg + backbone"),
            BOQItem(sno=7, item="GI Conduit 20mm",
                    description="GI conduit 20mm dia, IS 9537, for cable protection, with bends and accessories",
                    unit="Rmt", quantity=conduit_length,
                    calculation_basis=f"80% of total cable length {cable_length}m"),
            BOQItem(sno=8, item="End-of-Line Resistor",
                    description="End of line resistor kit for supervised loop circuit",
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

    if std == "NFPA":
        notes_std = "NFPA 72 (Fire Alarm), NFPA 13 (Sprinklers), NFPA 14 (Standpipe), NFPA 10 (Extinguishers)"
    else:
        notes_std = "NBC 2016 Part 4, IS 2189:2008, IS 15105, IS 3844"

    boq = {
        "sections": [section_a.dict(), section_b.dict(), section_c.dict()],
        "total_items": total_items,
        "standard": std,
        "boq_type": "manual",
        "ai_model": "",
        "notes": (
            f"BOQ generated as per {notes_std}. "
            f"Quantities are based on building analysis data. "
            f"Final quantities to be verified on site. "
            f"Building area: {round(area)} sqm, Floors: {floors}, Hazard: {hazard_category.upper()}."
        ),
    }
    return boq
