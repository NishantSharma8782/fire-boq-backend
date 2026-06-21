"""
AI BOQ Service — Generates Fire BOQ using AI models (Gemini, OpenAI, Groq, Claude).
Sends building dimensions + standard rules to the selected AI model and
parses the structured JSON response into BOQ sections.
Falls back to manual engine on failure.
"""
import json
import re
import asyncio
from typing import Optional
from app.config import get_settings

settings = get_settings()

MODEL_DISPLAY_NAMES = {
    "gemini": "Gemini 2.0 Flash",
    "openai": "GPT-4o",
    "groq": "Groq LLaMA-3.3",
    "claude": "Claude 3.5 Sonnet",
}


# ── JSON extraction ────────────────────────────────────────────────────────────
def _extract_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


# ── BOQ Prompt Builder ─────────────────────────────────────────────────────────
def _build_prompt(building_data: dict, recommendations: dict, project: dict, standard: str) -> str:
    area = building_data.get("estimated_area", 100)
    floors = building_data.get("floors", 1)
    rooms = building_data.get("rooms", 0)
    corridors = building_data.get("corridors", 0)
    stairs = building_data.get("stairs", 0)
    building_type = building_data.get("building_type", "office")
    hazard = project.get("hazard_category", "light")

    if standard == "NFPA":
        std_rules = """NFPA Standards:
- NFPA 72 (Fire Alarm): Smoke detector max 9.1m radius, pull station within 60m travel
- NFPA 13 (Sprinklers): Light hazard 18.6 sqm/head, Ordinary 12.1 sqm/head, Extra 9.3 sqm/head
- NFPA 14 (Standpipe): 65mm wet standpipe per floor
- NFPA 10 (Extinguishers): Light 278 sqm/unit, Ordinary 139 sqm/unit"""
    else:
        std_rules = """NBC / IS Standards:
- IS 2189 (Fire Alarm): Smoke detector 60 sqm (light), 40 sqm (ordinary), 30 sqm (high)
- IS 15105 (Sprinklers): 12 sqm/head (light), 9 sqm/head (ordinary), 7 sqm/head (high)
- IS 3844 (Hydrants): 1 per 500 sqm, max 30m between hydrants
- IS 2190 (Extinguishers): 100 sqm (light), 75 sqm (ordinary), 50 sqm (high)"""

    return f"""You are a fire safety engineering expert. Generate a detailed Fire Safety BOQ for the following building.

Building Details:
- Type: {building_type}
- Total Area: {area} sqm
- Floors: {floors}
- Rooms: {rooms}
- Corridors: {corridors}
- Staircases: {stairs}
- Hazard Category: {hazard}

Pre-calculated quantities to use:
- Smoke Detectors: {recommendations.get('smoke_detectors', 2)}
- Heat Detectors: {recommendations.get('heat_detectors', 1)}
- Manual Call Points: {recommendations.get('mcp', 2)}
- Hooters/Sounders: {recommendations.get('hooters', 2)}
- Fire Hydrants: {recommendations.get('hydrants', 1)}
- Sprinkler Heads: {recommendations.get('sprinklers', 4)}
- Hose Reels: {recommendations.get('hose_reels', 1)}
- Fire Alarm Panel: {recommendations.get('fire_alarm_panel', 1)}

Standard: {standard}
{std_rules}

Return ONLY valid JSON (no markdown, no explanation) in this exact format:
{{
  "sections": [
    {{
      "section_id": "A",
      "section_name": "Fire Hydrant System",
      "items": [
        {{
          "sno": 1,
          "item": "item name",
          "description": "detailed spec with standard ref",
          "unit": "Nos/Rmt/Lot",
          "quantity": 5.0,
          "calculation_basis": "how quantity was calculated"
        }}
      ]
    }},
    {{
      "section_id": "B",
      "section_name": "Fire Sprinkler System",
      "items": []
    }},
    {{
      "section_id": "C",
      "section_name": "Fire Alarm System",
      "items": []
    }}
  ],
  "notes": "brief notes about standards used"
}}

Include all major fire safety components. Each section should have 8-12 items with pipe lengths, fittings, valves, detectors, cables, panels etc. Calculate pipe lengths based on floor area and number of floors. Be precise and professional."""


# ── Individual model callers ───────────────────────────────────────────────────

async def _call_gemini(prompt: str) -> Optional[dict]:
    try:
        from google import genai
        client = genai.Client(api_key=settings.gemini_api_key)
        loop = asyncio.get_event_loop()
        def _do():
            return client.models.generate_content(model="gemini-2.0-flash", contents=[prompt])
        response = await loop.run_in_executor(None, _do)
        text = response.text.strip() if response.text else ""
        return _extract_json(text)
    except Exception as e:
        print(f"[AI_BOQ] Gemini error: {e}")
        return None


async def _call_openai(prompt: str) -> Optional[dict]:
    api_key = getattr(settings, "openai_api_key", "") or ""
    if not api_key:
        raise ValueError("OPENAI_API_KEY not configured in backend/.env")
    try:
        import openai
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a fire safety engineering expert. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=4000,
        )
        return _extract_json(response.choices[0].message.content or "")
    except Exception as e:
        print(f"[AI_BOQ] OpenAI error: {e}")
        raise


async def _call_groq(prompt: str) -> Optional[dict]:
    """Call Groq (LLaMA-3.3-70B) for BOQ generation."""
    api_key = getattr(settings, "groq_api_key", "") or ""
    if not api_key:
        raise ValueError("GROQ_API_KEY not configured in backend/.env")
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=api_key)
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a fire safety engineering expert. Return only valid JSON, no markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=4000,
        )
        return _extract_json(response.choices[0].message.content or "")
    except Exception as e:
        print(f"[AI_BOQ] Groq error: {e}")
        raise


async def _call_claude(prompt: str) -> Optional[dict]:
    """Call Anthropic Claude 3.5 Sonnet for BOQ generation."""
    api_key = getattr(settings, "claude_api_key", "") or ""
    if not api_key:
        raise ValueError("CLAUDE_API_KEY not configured in backend/.env")
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4000,
            system="You are a fire safety engineering expert. Return only valid JSON, no markdown.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
        return _extract_json(text)
    except Exception as e:
        print(f"[AI_BOQ] Claude error: {e}")
        raise


# ── Section validator ──────────────────────────────────────────────────────────
def _validate_and_build_sections(data: dict) -> list:
    sections = data.get("sections", [])
    result = []
    for sec in sections:
        items = []
        for i, item in enumerate(sec.get("items", []), 1):
            items.append({
                "sno": item.get("sno", i),
                "item": str(item.get("item", ""))[:200],
                "description": str(item.get("description", ""))[:500],
                "unit": str(item.get("unit", "Nos"))[:20],
                "quantity": max(0.0, float(item.get("quantity", 0))),
                "calculation_basis": str(item.get("calculation_basis", ""))[:300],
            })
        result.append({
            "section_id": str(sec.get("section_id", "A"))[:5],
            "section_name": str(sec.get("section_name", ""))[:100],
            "items": items,
        })
    return result


# ── Main entry ─────────────────────────────────────────────────────────────────
async def generate_ai_boq(
    building_data: dict,
    recommendations: dict,
    project: dict,
    standard: str = "NBC",
    ai_model: str = "gemini",
) -> dict:
    """
    Generate BOQ using the selected AI model (Gemini/OpenAI/Groq/Claude).
    Falls back to manual engine on failure.
    """
    from app.services.boq_engine import generate_boq

    prompt = _build_prompt(building_data, recommendations, project, standard)
    model = ai_model.lower()
    display_name = MODEL_DISPLAY_NAMES.get(model, model.capitalize())

    ai_data = None
    error_msg = ""

    try:
        if model == "openai":
            ai_data = await _call_openai(prompt)
        elif model == "groq":
            ai_data = await _call_groq(prompt)
        elif model == "claude":
            ai_data = await _call_claude(prompt)
        else:
            ai_data = await _call_gemini(prompt)
    except Exception as e:
        error_msg = str(e)
        print(f"[AI_BOQ] {display_name} failed: {e}")

    if not ai_data or not ai_data.get("sections"):
        print(f"[AI_BOQ] Falling back to manual engine. Reason: {error_msg or 'empty response'}")
        fallback = generate_boq(
            project=project,
            building_data=building_data,
            recommendations=recommendations,
            hazard_category=project.get("hazard_category", "light"),
            standard=standard,
        )
        fallback["boq_type"] = "ai_fallback"
        fallback["ai_model"] = model
        err_short = error_msg[:120] if error_msg else "AI returned empty response"
        fallback["notes"] = (
            f"[Note: {display_name} BOQ generation failed — {err_short}. "
            f"Showing manual calculation instead.] " + fallback.get("notes", "")
        )
        return fallback

    sections = _validate_and_build_sections(ai_data)
    total_items = sum(len(s["items"]) for s in sections)

    area = float(building_data.get("estimated_area", 100))
    floors = int(building_data.get("floors", 1))
    hazard = project.get("hazard_category", "light")
    notes_suffix = ai_data.get("notes", "")
    notes_std = "NFPA 72, NFPA 13, NFPA 14, NFPA 10" if standard == "NFPA" else "NBC 2016 Part 4, IS 2189, IS 15105, IS 3844"

    return {
        "sections": sections,
        "total_items": total_items,
        "standard": standard.upper(),
        "boq_type": "ai",
        "ai_model": model,
        "notes": (
            f"AI-generated BOQ using {display_name} as per {notes_std}. "
            f"Building area: {round(area)} sqm, Floors: {floors}, Hazard: {hazard.upper()}. "
            f"{notes_suffix}"
        ),
    }
