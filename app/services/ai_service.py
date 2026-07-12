"""
Unified AI Service — Routes all AI calls (drawing analysis, BOQ, chat) to the
model selected for a project: Gemini, OpenAI, Groq, or Claude.

Models supported:
  gemini  → Google Gemini 2.0 Flash (vision + text)
  openai  → OpenAI GPT-4o (vision + text)
  groq    → Groq LLaMA-3.3 70B (text only — Gemini handles image analysis)
  claude  → Anthropic Claude 3.5 Sonnet (vision + text)

IMPORTANT:
  - Drawing/image analysis: Groq has NO vision, so Gemini is ALWAYS used for
    image analysis regardless of the selected model. No error is raised.
  - BOQ generation: Groq CAN generate text BOQs. If Groq fails, Gemini retries.
  - Chat: Groq works fine for text chat.
"""
import json
import re
import asyncio
import base64
from pathlib import Path
from typing import Optional

from app.config import get_settings

settings = get_settings()

# ── Supported models ──────────────────────────────────────────────────────────
VISION_CAPABLE = {"gemini", "openai", "claude"}  # groq = text-only

MODEL_LABELS = {
    "gemini": "Gemini 2.0 Flash",
    "openai": "GPT-4o",
    "groq": "Groq LLaMA-3.3-70B",
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


# ── Image loading ──────────────────────────────────────────────────────────────
def _load_image_b64(file_path: str) -> tuple[bytes, str]:
    """Returns (image_bytes, mime_type) for image-capable model calls."""
    from PIL import Image
    import io
    path = Path(file_path)
    if path.suffix.lower() == ".pdf":
        from pdf2image import convert_from_path
        pages = convert_from_path(file_path, first_page=1, last_page=1, dpi=100)
        img = pages[0].convert("RGB")
    else:
        img = Image.open(file_path).convert("RGB")
    img.thumbnail((1024, 768), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70, optimize=True)
    return buf.getvalue(), "image/jpeg"


# ── Drawing analysis prompt ────────────────────────────────────────────────────
ANALYSIS_PROMPT = (
    "You are a fire safety engineer analyzing a building floor plan image.\n"
    "Building Type context: {building_type}, Hazard Category: {hazard_category}.\n\n"
    "IMPORTANT RULES:\n"
    "1. ONLY extract values you can actually SEE in the drawing. Do NOT guess or make up numbers.\n"
    "2. If the image is blurry, incomplete, not a floor plan, or unreadable: set extraction_failed=true and explain in failure_reason.\n"
    "3. For estimated_area: measure or estimate from visible rooms/spaces. Do NOT default to 500.\n"
    "4. For floors: only count floors shown or labeled. Do NOT default to 3.\n\n"
    "Return ONLY valid JSON (no markdown, no extra text):\n"
    '{{"extraction_failed":false,"failure_reason":"",'
    '"building_type":"<detected type>","rooms":<int>,'
    '"estimated_area":<actual sqm float from drawing>,'
    '"floors":<int>,"corridors":<int>,"stairs":<int>,"entrances":<int>,"exits":<int>,'
    '"open_areas":<int>,"ceiling_height":<meters float>,'
    '"description":"<what you actually see in the drawing>"}}\n'
    "If extraction_failed is true, all numeric fields must be 0."
)


# ─────────────────────────────────────────────────────────────────────────────
# Drawing Analysis
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_drawing(
    file_path: str,
    building_type: str,
    hazard_category: str,
    ai_model: str = "gemini",
) -> dict:
    """
    Analyze a building drawing image using an AI vision model.

    GROQ SPECIAL CASE: Groq LLaMA-3.3 has NO vision capability. When the project
    uses Groq, we silently use Gemini for image analysis instead — no error is
    shown to the user. The result is tagged with data_source='ai_gemini_vision'.

    Returns: {success, building_data, raw} or {success=False, error_code, error_message}
    """
    model = ai_model.lower()

    if model == "openai":
        return await _openai_analyze(file_path, building_type, hazard_category)

    elif model == "claude":
        return await _claude_analyze(file_path, building_type, hazard_category)

    else:
        # gemini, groq (no vision → use gemini), or any unknown model
        # Always use Gemini for image analysis — it's the most capable vision model
        from app.services import gemini_service
        result = await gemini_service.analyze_drawing(file_path, building_type, hazard_category)
        if model == "groq" and result.get("success"):
            # Tag that Gemini handled the vision part transparently
            result["vision_model"] = "gemini"
            result["raw"] = "[Image analyzed by Gemini 2.0 Flash — Groq is text-only] " + result.get("raw", "")
        return result


async def _openai_analyze(file_path: str, building_type: str, hazard_category: str) -> dict:
    api_key = getattr(settings, "openai_api_key", "") or ""
    if not api_key:
        return {
            "success": False,
            "error_code": "API_KEY_INVALID",
            "error_message": "OPENAI_API_KEY not configured in backend/.env. Please add it or use Gemini.",
            "building_data": None,
        }
    try:
        import openai
        img_bytes, mime = _load_image_b64(file_path)
        b64 = base64.b64encode(img_bytes).decode()
        client = openai.AsyncOpenAI(api_key=api_key)
        prompt = ANALYSIS_PROMPT.format(building_type=building_type, hazard_category=hazard_category)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            max_tokens=800,
            temperature=0.1,
        )
        raw = response.choices[0].message.content or ""
        data = _extract_json(raw)
        if not data:
            return {"success": False, "error_code": "EMPTY_RESPONSE", "error_message": "GPT-4o returned no structured data.", "building_data": None}
        # Check if AI flagged extraction failure
        if data.get("extraction_failed"):
            return {
                "success": False,
                "error_code": "IMAGE_UNREADABLE",
                "error_message": f"GPT-4o could not extract data from the drawing: {data.get('failure_reason', 'Image unclear or not a floor plan')}. Please upload a clearer drawing or use Manual Entry.",
                "building_data": None,
            }
        return {"success": True, "building_data": _sanitize(data, building_type), "raw": raw}
    except Exception as e:
        return {"success": False, "error_code": "UNKNOWN_ERROR", "error_message": f"OpenAI error: {str(e)[:200]}", "building_data": None}


async def _claude_analyze(file_path: str, building_type: str, hazard_category: str) -> dict:
    api_key = getattr(settings, "claude_api_key", "") or ""
    if not api_key:
        return {
            "success": False,
            "error_code": "API_KEY_INVALID",
            "error_message": "CLAUDE_API_KEY not configured in backend/.env. Please add it or use Gemini.",
            "building_data": None,
        }
    try:
        import anthropic
        img_bytes, mime = _load_image_b64(file_path)
        b64 = base64.b64encode(img_bytes).decode()
        client = anthropic.AsyncAnthropic(api_key=api_key)
        prompt = ANALYSIS_PROMPT.format(building_type=building_type, hazard_category=hazard_category)
        response = await client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = response.content[0].text if response.content else ""
        data = _extract_json(raw)
        if not data:
            return {"success": False, "error_code": "EMPTY_RESPONSE", "error_message": "Claude returned no structured data.", "building_data": None}
        # Check if AI flagged extraction failure
        if data.get("extraction_failed"):
            return {
                "success": False,
                "error_code": "IMAGE_UNREADABLE",
                "error_message": f"Claude could not extract data from the drawing: {data.get('failure_reason', 'Image unclear or not a floor plan')}. Please upload a clearer drawing or use Manual Entry.",
                "building_data": None,
            }
        return {"success": True, "building_data": _sanitize(data, building_type), "raw": raw}
    except Exception as e:
        return {"success": False, "error_code": "UNKNOWN_ERROR", "error_message": f"Claude error: {str(e)[:200]}", "building_data": None}


# NOTE: _groq_analyze_text() removed.
# Groq image analysis now silently uses Gemini (see analyze_drawing above).


def _sanitize(data: dict, building_type: str) -> dict:
    return {
        "building_type": str(data.get("building_type", building_type))[:100],
        "rooms": max(0, int(data.get("rooms", 0))),
        "estimated_area": max(1.0, float(data.get("estimated_area", 0))),
        "floors": max(1, int(data.get("floors", 1))),
        "corridors": max(0, int(data.get("corridors", 0))),
        "stairs": max(0, int(data.get("stairs", 0))),
        "entrances": max(0, int(data.get("entrances", 0))),
        "exits": max(0, int(data.get("exits", 0))),
        "open_areas": max(0, int(data.get("open_areas", 0))),
        "ceiling_height": max(2.0, float(data.get("ceiling_height", 3.0))),
        "description": str(data.get("description", ""))[:500],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Chat / AI Assistant
# ─────────────────────────────────────────────────────────────────────────────

CHAT_SYSTEM = (
    "You are an expert fire safety engineering assistant for the Fire BOQ Platform. "
    "Help engineers understand fire system designs, BOQ calculations, and fire safety standards. "
    "Reference NBC 2016, IS 2189, IS 15105, NFPA 72, NFPA 13 where relevant. "
    "Be concise but thorough. Use specific numbers from the project context."
)


async def chat_with_context(
    message: str,
    project_context: dict,
    history: list,
    ai_model: str = "gemini",
) -> str:
    """Route chat to the selected AI model."""
    model = ai_model.lower()

    context_str = json.dumps(project_context, indent=2, default=str)
    history_str = ""
    for msg in history[-6:]:
        role = "User" if msg.get("role") == "user" else "Assistant"
        history_str += f"{role}: {msg.get('content', '')}\n"

    prompt = (
        f"Project Context:\n{context_str}\n\n"
        f"Conversation:\n{history_str}\n"
        f"User: {message}\n\nAnswer:"
    )

    if model == "gemini":
        from app.services import gemini_service
        return await gemini_service.chat_with_context(message, project_context, history)

    elif model == "openai":
        return await _openai_chat(prompt)

    elif model == "groq":
        return await _groq_chat(prompt)

    elif model == "claude":
        return await _claude_chat(prompt)

    else:
        from app.services import gemini_service
        return await gemini_service.chat_with_context(message, project_context, history)


async def _openai_chat(prompt: str) -> str:
    api_key = getattr(settings, "openai_api_key", "") or ""
    if not api_key:
        return "⚠️ OpenAI API key not configured. Add OPENAI_API_KEY to backend/.env to use GPT-4o."
    try:
        import openai
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": CHAT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1000,
            temperature=0.3,
        )
        return response.choices[0].message.content or "No response from GPT-4o."
    except Exception as e:
        return f"GPT-4o error: {str(e)[:200]}"


async def _groq_chat(prompt: str) -> str:
    api_key = getattr(settings, "groq_api_key", "") or ""
    if not api_key:
        return "⚠️ Groq API key not configured. Add GROQ_API_KEY to backend/.env to use Groq."
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=api_key)
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": CHAT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1000,
            temperature=0.3,
        )
        return response.choices[0].message.content or "No response from Groq."
    except Exception as e:
        return f"Groq error: {str(e)[:200]}"


async def _claude_chat(prompt: str) -> str:
    api_key = getattr(settings, "claude_api_key", "") or ""
    if not api_key:
        return "⚠️ Claude API key not configured. Add CLAUDE_API_KEY to backend/.env to use Claude."
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            system=CHAT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text if response.content else "No response from Claude."
    except Exception as e:
        return f"Claude error: {str(e)[:200]}"


# ─────────────────────────────────────────────────────────────────────────────
# AI BOQ Generation (re-exports from ai_boq_service)
# ─────────────────────────────────────────────────────────────────────────────

async def generate_ai_boq_for_model(
    building_data: dict,
    recommendations: dict,
    project: dict,
    standard: str = "NBC",
    ai_model: str = "gemini",
) -> dict:
    """Wrapper to generate AI BOQ using the project's selected model."""
    from app.services.ai_boq_service import generate_ai_boq
    return await generate_ai_boq(
        building_data=building_data,
        recommendations=recommendations,
        project=project,
        standard=standard,
        ai_model=ai_model,
    )
