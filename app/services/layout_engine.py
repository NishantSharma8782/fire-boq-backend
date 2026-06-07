"""
Layout Engine - Converts fire equipment counts into 2D canvas coordinates.
Uses grid-based placement algorithms to position fire equipment on a virtual canvas.
"""
import math
from typing import List, Dict


def generate_layout(building_data: dict, recommendations: dict) -> dict:
    """
    Generate 2D layout coordinates for fire equipment visualization.
    Returns canvas-ready coordinate data for Konva.js rendering.
    """
    area = float(building_data.get("estimated_area", 100))
    rooms = int(building_data.get("rooms", 1))
    floors = int(building_data.get("floors", 1))

    # Canvas dimensions (fixed at 800×600 for display)
    CANVAS_W = 800
    CANVAS_H = 600
    PADDING = 60  # Border padding

    usable_w = CANVAS_W - 2 * PADDING
    usable_h = CANVAS_H - 2 * PADDING

    # Approximate building scale: area → pixel dimensions
    side = math.sqrt(area)
    scale_x = usable_w / max(side, 1)
    scale_y = usable_h / max(side, 1)
    scale = min(scale_x, scale_y, 2.0)  # cap at 2× zoom

    bld_w = min(side * scale, usable_w)
    bld_h = min(side * scale, usable_h)

    # Building outline (rectangle)
    bx = PADDING
    by = PADDING
    building_outline = [
        {"x": bx, "y": by},
        {"x": bx + bld_w, "y": by},
        {"x": bx + bld_w, "y": by + bld_h},
        {"x": bx, "y": by + bld_h},
        {"x": bx, "y": by},  # close
    ]

    def grid_points(count: int, margin: int = 30) -> List[Dict]:
        """Place items in a grid within building bounds."""
        if count <= 0:
            return []
        cols = max(1, math.ceil(math.sqrt(count)))
        rows = max(1, math.ceil(count / cols))
        step_x = (bld_w - 2 * margin) / max(cols, 1)
        step_y = (bld_h - 2 * margin) / max(rows, 1)
        points = []
        idx = 0
        for r in range(rows):
            for c in range(cols):
                if idx >= count:
                    break
                points.append({
                    "x": round(bx + margin + c * step_x + step_x / 2, 1),
                    "y": round(by + margin + r * step_y + step_y / 2, 1),
                    "label": f"SD-{idx + 1}",
                })
                idx += 1
        return points

    def perimeter_points(count: int, label_prefix: str, offset: int = 0) -> List[Dict]:
        """Place items along building perimeter."""
        if count <= 0:
            return []
        perimeter = 2 * (bld_w + bld_h)
        spacing = perimeter / max(count, 1)
        points = []
        for i in range(count):
            dist = i * spacing + offset
            if dist < bld_w:
                px, py = bx + dist, by + 20
            elif dist < bld_w + bld_h:
                px, py = bx + bld_w - 20, by + (dist - bld_w)
            elif dist < 2 * bld_w + bld_h:
                px, py = bx + bld_w - (dist - bld_w - bld_h), by + bld_h - 20
            else:
                px, py = bx + 20, by + bld_h - (dist - 2 * bld_w - bld_h)
            points.append({"x": round(px, 1), "y": round(py, 1), "label": f"{label_prefix}-{i + 1}"})
        return points

    def edge_points(count: int, label_prefix: str, side: str = "left") -> List[Dict]:
        """Place items along one edge of the building."""
        if count <= 0:
            return []
        points = []
        spacing = bld_h / max(count + 1, 2)
        for i in range(count):
            if side == "left":
                px, py = bx + 20, by + spacing * (i + 1)
            else:
                px, py = bx + bld_w - 20, by + spacing * (i + 1)
            points.append({"x": round(px, 1), "y": round(py, 1), "label": f"{label_prefix}-{i + 1}"})
        return points

    smoke_detectors = recommendations.get("smoke_detectors", 0)
    heat_detectors = recommendations.get("heat_detectors", 0)
    mcp_count = recommendations.get("mcp", 0)
    hooter_count = recommendations.get("hooters", 0)
    sprinkler_count = recommendations.get("sprinklers", 0)
    hydrant_count = recommendations.get("hydrants", 0)
    extinguisher_count = recommendations.get("fire_extinguishers", 0)

    # Generate positions
    sd_points = grid_points(smoke_detectors, margin=30)
    for i, p in enumerate(sd_points):
        p["label"] = f"SD-{i + 1}"

    hd_points = grid_points(heat_detectors, margin=50)
    for i, p in enumerate(hd_points):
        p["label"] = f"HD-{i + 1}"

    mcp_points = edge_points(mcp_count, "MCP", side="left")
    hooter_points = edge_points(hooter_count, "HT", side="right")
    sprinkler_points = grid_points(sprinkler_count, margin=15)
    for i, p in enumerate(sprinkler_points):
        p["label"] = f"SP-{i + 1}"

    hydrant_points = perimeter_points(hydrant_count, "HY", offset=40)
    ext_points = perimeter_points(extinguisher_count, "EX", offset=80)

    return {
        "canvas_width": CANVAS_W,
        "canvas_height": CANVAS_H,
        "scale": round(scale, 2),
        "building_outline": building_outline,
        "smoke_detectors": sd_points,
        "heat_detectors": hd_points,
        "mcp": mcp_points,
        "hooters": hooter_points,
        "sprinklers": sprinkler_points,
        "hydrants": hydrant_points,
        "fire_extinguishers": ext_points,
    }
