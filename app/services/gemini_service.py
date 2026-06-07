"""
Gemini Service — optimized for minimal token usage + 429 retry handling.
- Images are resized to max 1024×768 and JPEG-compressed (quality=70) before upload
- Only ONE Gemini call per analysis (drawing analysis only)
- Placement strategy is generated locally by the BOQ engine (no second Gemini call)
- All errors are classified and surfaced — NO silent fake/default fallbacks
- Exponential backoff with jitter for 429 RESOURCE_EXHAUSTED errors (free tier safe)
- AsyncRateLimiter ensures max 10 req/min to stay within free tier limits
"""
import json
import re
import asyncio
import time
import random
from pathlib import Path
from PIL import Image
import io
from google import genai
from google.genai import types
from app.config import get_settings

settings = get_settings()

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_IMG_WIDTH = 1024
MAX_IMG_HEIGHT = 768
JPEG_QUALITY = 70

# Retry config for 429 errors (free tier: 15 RPM, so we back off generously)
MAX_RETRIES = 4          # up to 4 retries after initial attempt
BASE_BACKOFF_SEC = 15    # start waiting 15s on first 429 (retryDelay from API is ~36s)
MAX_BACKOFF_SEC = 120    # never wait more than 2 minutes

# Rate limiter: max 10 calls per 60 seconds (safe under 15 RPM free limit)
_RATE_LIMIT_CALLS = 10
_RATE_LIMIT_WINDOW = 60  # seconds

# ── Error codes ───────────────────────────────────────────────────────────────
ERROR_RATE_LIMIT = "API_RATE_LIMIT"
ERROR_KEY_INVALID = "API_KEY_INVALID"
ERROR_TIMEOUT = "API_TIMEOUT"
ERROR_IMAGE_UNREADABLE = "IMAGE_UNREADABLE"
ERROR_EMPTY_RESPONSE = "EMPTY_RESPONSE"
ERROR_UNKNOWN = "UNKNOWN_ERROR"


# ── Async Rate Limiter ────────────────────────────────────────────────────────
class AsyncRateLimiter:
    """
    Simple sliding-window rate limiter.
    Ensures we don't exceed _RATE_LIMIT_CALLS calls per _RATE_LIMIT_WINDOW seconds.
    """

    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            # Remove timestamps outside the window
            self._calls = [t for t in self._calls if now - t < self.window]
            if len(self._calls) >= self.max_calls:
                # Wait until the oldest call falls out of the window
                oldest = self._calls[0]
                wait_time = self.window - (now - oldest) + 0.1
                print(f"[RateLimiter] Throttling — waiting {wait_time:.1f}s to stay within free-tier limits")
                await asyncio.sleep(wait_time)
                now = time.monotonic()
                self._calls = [t for t in self._calls if now - t < self.window]
            self._calls.append(time.monotonic())


_rate_limiter = AsyncRateLimiter(_RATE_LIMIT_CALLS, _RATE_LIMIT_WINDOW)


# ── Gemini client ─────────────────────────────────────────────────────────────
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


# ── Error classification ──────────────────────────────────────────────────────
def _classify_error(exc: Exception) -> tuple[str, str]:
    """
    Returns (error_code, user_friendly_message) from an exception.
    Never returns vague messages — always tells the user what happened.
    """
    msg = str(exc).lower()

    if any(kw in msg for kw in ["429", "quota", "rate limit", "resource_exhausted", "ratelerror"]):
        return (
            ERROR_RATE_LIMIT,
            "Gemini API rate limit exceeded (free tier quota). "
            "The system will automatically retry. If this persists, please wait 1 minute and try again.",
        )
    if any(kw in msg for kw in ["401", "403", "api_key", "invalid key", "authentication", "permission"]):
        return (
            ERROR_KEY_INVALID,
            "Gemini API key is invalid or expired. "
            "Please enter building measurements manually.",
        )
    if any(kw in msg for kw in ["timeout", "timed out", "deadline"]):
        return (
            ERROR_TIMEOUT,
            "Gemini API request timed out. The image may be too large or the service is slow. "
            "Please enter building measurements manually.",
        )
    if any(kw in msg for kw in ["cannot identify image", "image format", "unreadable", "broken"]):
        return (
            ERROR_IMAGE_UNREADABLE,
            "The uploaded drawing could not be read as an image. "
            "Please check the file format (PDF/PNG/JPG) and try again, or enter measurements manually.",
        )
    return (
        ERROR_UNKNOWN,
        f"AI analysis failed: {str(exc)[:200]}. Please enter building measurements manually.",
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if this exception is a 429 / quota error worth retrying."""
    msg = str(exc).lower()
    return any(kw in msg for kw in ["429", "quota", "rate limit", "resource_exhausted"])


def _parse_retry_delay(exc: Exception) -> float | None:
    """
    Try to extract the retryDelay from the API error body (e.g. '36s').
    Returns seconds as float, or None if not found.
    """
    try:
        raw = str(exc)
        m = re.search(r"'retryDelay':\s*'(\d+)s'", raw)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return None


# ── Retry wrapper ─────────────────────────────────────────────────────────────
async def _call_with_retry(fn, *args, **kwargs):
    """
    Call an async or sync callable, retrying on 429 with exponential backoff + jitter.
    fn must be a coroutine function (async def) or a plain callable.
    """
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            await _rate_limiter.acquire()
            if asyncio.iscoroutinefunction(fn):
                return await fn(*args, **kwargs)
            else:
                # Run sync Gemini SDK call in threadpool so we don't block the event loop
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
        except Exception as exc:
            last_exc = exc
            if _is_rate_limit_error(exc) and attempt < MAX_RETRIES:
                # Try to use the API's suggested retryDelay, else exponential backoff
                suggested = _parse_retry_delay(exc)
                if suggested:
                    wait = min(suggested + random.uniform(1, 5), MAX_BACKOFF_SEC)
                else:
                    wait = min(BASE_BACKOFF_SEC * (2 ** attempt) + random.uniform(0, 3), MAX_BACKOFF_SEC)
                print(
                    f"[GeminiService] 429 rate-limit hit (attempt {attempt + 1}/{MAX_RETRIES + 1}). "
                    f"Retrying in {wait:.1f}s…"
                )
                await asyncio.sleep(wait)
            else:
                raise  # Non-retryable error or exhausted retries
    raise last_exc  # Should never reach here, but satisfy type checker


# ── Image preparation ─────────────────────────────────────────────────────────
def _prepare_image(file_path: str) -> tuple[bytes, str]:
    """
    Load, resize, and JPEG-compress an image for minimal token usage.
    PDF: convert first page to image.
    Returns (jpeg_bytes, 'image/jpeg').
    """
    path = Path(file_path)

    if path.suffix.lower() == ".pdf":
        try:
            from pdf2image import convert_from_path
            pages = convert_from_path(file_path, first_page=1, last_page=1, dpi=100)
            if not pages:
                raise ValueError("PDF produced no pages")
            img = pages[0].convert("RGB")
        except Exception as pdf_err:
            raise ValueError(f"cannot identify image file from PDF: {pdf_err}") from pdf_err
    else:
        img = Image.open(file_path).convert("RGB")

    # Resize to fit within MAX dimensions (preserving aspect ratio)
    img.thumbnail((MAX_IMG_WIDTH, MAX_IMG_HEIGHT), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue(), "image/jpeg"


# ── JSON extraction ───────────────────────────────────────────────────────────
def _extract_json(text: str) -> dict | None:
    """Extract JSON from Gemini response. Returns None if not parseable."""
    # Direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Markdown code block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass

    # Raw JSON object in text
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass

    return None


# ── Main analysis function ────────────────────────────────────────────────────
async def analyze_drawing(file_path: str, building_type: str, hazard_category: str) -> dict:
    """
    Send drawing to Gemini Vision for building analysis.
    Automatically retries on 429 with exponential backoff.

    Returns:
        On success: {"success": True, "building_data": {...}, "raw": "..."}
        On failure: {"success": False, "error_code": "...", "error_message": "...", "building_data": None}

    IMPORTANT: Never returns fake/default building_data on failure.
    The caller must handle success=False by requiring manual entry.
    """
    client = _get_client()

    # Compact prompt — minimum tokens while preserving accuracy
    prompt = (
        f"Analyze this building floor plan. Building type: {building_type}, hazard: {hazard_category}.\n"
        "Return ONLY valid JSON (no markdown, no extra text):\n"
        '{"building_type":"<detected type>","rooms":<int>,"estimated_area":<sqm float>,'
        '"floors":<int>,"corridors":<int>,"stairs":<int>,"entrances":<int>,"exits":<int>,'
        '"open_areas":<int>,"ceiling_height":<meters float>,"description":"<brief>"}\n'
        "Use reasonable estimates. estimated_area in square meters."
    )

    try:
        img_bytes, mime_type = _prepare_image(file_path)
    except Exception as e:
        code, message = _classify_error(e)
        return {"success": False, "error_code": code, "error_message": message, "building_data": None}

    def _do_generate():
        return client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type=mime_type),
                prompt,
            ],
        )

    try:
        response = await _call_with_retry(_do_generate)
        raw_text = response.text.strip() if response.text else ""
    except Exception as e:
        code, message = _classify_error(e)
        print(f"[GeminiService] API error ({code}): {e}")
        return {"success": False, "error_code": code, "error_message": message, "building_data": None}

    if not raw_text:
        return {
            "success": False,
            "error_code": ERROR_EMPTY_RESPONSE,
            "error_message": "Gemini returned an empty response. The image may be unreadable. Please enter measurements manually.",
            "building_data": None,
        }

    data = _extract_json(raw_text)
    if not data:
        return {
            "success": False,
            "error_code": ERROR_IMAGE_UNREADABLE,
            "error_message": (
                "Gemini could not extract structured data from the drawing. "
                "The image quality may be too low, or this is not a floor plan. "
                "Please enter building measurements manually."
            ),
            "building_data": None,
        }

    # Validate and sanitise extracted values
    building_data = {
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

    # Sanity check: if area is suspiciously 0 or rooms+area both missing
    if building_data["estimated_area"] < 10 and building_data["rooms"] == 0:
        return {
            "success": False,
            "error_code": ERROR_IMAGE_UNREADABLE,
            "error_message": (
                "Gemini extracted implausible building data (area < 10 sqm, 0 rooms). "
                "The drawing may not be a recognizable floor plan. "
                "Please verify the file and try again, or enter measurements manually."
            ),
            "building_data": None,
        }

    return {"success": True, "building_data": building_data, "raw": raw_text}


# ── Chat assistant ────────────────────────────────────────────────────────────
async def chat_with_context(message: str, project_context: dict, history: list) -> str:
    """AI assistant for explaining BOQ and recommendations. Auto-retries on 429."""
    client = _get_client()

    context_str = json.dumps(project_context, indent=2, default=str)
    history_str = ""
    for msg in history[-6:]:
        role = "User" if msg.get("role") == "user" else "Assistant"
        history_str += f"{role}: {msg.get('content', '')}\n"

    prompt = (
        "You are an expert fire safety engineering assistant for the Fire BOQ Platform.\n"
        "Help engineers understand fire system designs, BOQ calculations, and Indian standards.\n\n"
        f"Project Context:\n{context_str}\n\n"
        f"Conversation:\n{history_str}\n"
        f"User: {message}\n\n"
        "Instructions: Answer based on context. Reference NBC 2016, IS 2189, IS 15105 where relevant. "
        "Be concise but thorough. Use specific numbers from the project.\n\nAnswer:"
    )

    def _do_chat():
        return client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[prompt],
        )

    try:
        response = await _call_with_retry(_do_chat)
        return response.text.strip()
    except Exception as e:
        code, friendly = _classify_error(e)
        return f"AI assistant unavailable ({code}): {friendly}"
