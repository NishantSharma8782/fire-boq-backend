"""
BOQ Agent Service — Uses AI (Groq/Gemini/OpenAI/Claude) to interpret natural
language instructions and apply targeted modifications to the BOQ in MongoDB.

Model priority: Uses the project's ai_model setting from ENV.
Default fallback: Groq (LLaMA-3.3-70B) → Gemini → error.

Supported intents:
  - update_quantity   : Change quantity of specific items/sections
  - remove_section    : Remove an entire section from BOQ
  - change_standard   : Regenerate BOQ with NBC/NFPA switch
  - add_item          : Add a new item to an existing section
  - regenerate_boq    : Fully regenerate BOQ from analysis data
  - update_project    : Update project fields (name, client, location, etc.)
  - custom_update     : AI decides how to modify based on instruction
"""
import json
import re
import asyncio
from typing import Optional
from app.config import get_settings

settings = get_settings()


# ── Case-insensitive text replacement ─────────────────────────────────────────
def _case_insensitive_replace(text: str, find: str, replace: str) -> str:
    """Replace all occurrences of `find` in `text` (case-insensitive), preserving surrounding text."""
    if not find or not text:
        return text
    return re.sub(re.escape(find), replace, text, flags=re.IGNORECASE)


# ── JSON extraction helper ─────────────────────────────────────────────────────
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


# ── Groq caller (default — LLaMA-3.3-70B) ────────────────────────────────────
async def _call_groq(prompt: str) -> Optional[dict]:
    api_key = getattr(settings, "groq_api_key", "") or ""
    if not api_key:
        print("[AGENT] GROQ_API_KEY not set — skipping Groq")
        return None
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=api_key)
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Fire Project & BOQ modification AI agent. "
                        "Return ONLY valid JSON, no markdown fences, no explanation."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        return _extract_json(response.choices[0].message.content or "")
    except Exception as e:
        print(f"[AGENT] Groq error: {e}")
        return None


# ── Gemini caller ──────────────────────────────────────────────────────────────
async def _call_gemini(prompt: str) -> Optional[dict]:
    api_key = getattr(settings, "gemini_api_key", "") or ""
    if not api_key:
        print("[AGENT] GEMINI_API_KEY not set — skipping Gemini")
        return None
    try:
        from google import genai
        import asyncio
        client = genai.Client(api_key=api_key)
        loop = asyncio.get_event_loop()
        def _do():
            return client.models.generate_content(model="gemini-2.0-flash", contents=[prompt])
        response = await loop.run_in_executor(None, _do)
        text = response.text.strip() if response.text else ""
        return _extract_json(text)
    except Exception as e:
        print(f"[AGENT] Gemini error: {e}")
        return None


# ── OpenAI caller ──────────────────────────────────────────────────────────────
async def _call_openai(prompt: str) -> Optional[dict]:
    api_key = getattr(settings, "openai_api_key", "") or ""
    if not api_key:
        print("[AGENT] OPENAI_API_KEY not set — skipping OpenAI")
        return None
    try:
        import openai
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Fire Project & BOQ modification AI agent. "
                        "Return ONLY valid JSON, no markdown."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        return _extract_json(response.choices[0].message.content or "")
    except Exception as e:
        print(f"[AGENT] OpenAI error: {e}")
        return None


# ── Claude caller ──────────────────────────────────────────────────────────────
async def _call_claude(prompt: str) -> Optional[dict]:
    api_key = getattr(settings, "claude_api_key", "") or ""
    if not api_key:
        print("[AGENT] CLAUDE_API_KEY not set — skipping Claude")
        return None
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2000,
            system=(
                "You are a Fire Project & BOQ modification AI agent. "
                "Return ONLY valid JSON, no markdown fences."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
        return _extract_json(text)
    except Exception as e:
        print(f"[AGENT] Claude error: {e}")
        return None


# ── Unified model router ───────────────────────────────────────────────────────
async def _call_ai_model(prompt: str, ai_model: str) -> Optional[dict]:
    """
    Route the agent prompt to the correct AI model based on project setting.
    Falls back to Groq → Gemini if primary model fails.
    """
    model = (ai_model or "groq").lower()
    print(f"[AGENT] Using model: {model}")

    # Primary call
    if model == "groq":
        result = await _call_groq(prompt)
    elif model == "openai":
        result = await _call_openai(prompt)
    elif model == "claude":
        result = await _call_claude(prompt)
    else:  # gemini or unknown
        result = await _call_gemini(prompt)

    if result:
        return result

    # Fallback chain: Groq → Gemini
    if model != "groq":
        print(f"[AGENT] {model} failed — retrying with Groq")
        result = await _call_groq(prompt)
        if result:
            return result

    if model not in ("gemini",):
        print("[AGENT] Groq also failed — retrying with Gemini")
        result = await _call_gemini(prompt)

    return result


# ── Validation prompt ────────────────────────────────────────────────────────
def _build_validation_prompt(instruction: str, project: dict) -> str:
    building_type = project.get("building_type", "office")
    return f"""You are a Fire Protection BOQ (Bill of Quantities) validation assistant.
Your job is to check if a user instruction is appropriate for a fire protection BOQ.

Project building type: {building_type}
User instruction: "{instruction}"

Valid fire BOQ instructions include:
- Changing quantities of fire equipment (smoke detectors, sprinklers, MCP, hooters, hydrants, hose reels, fire extinguishers, fire alarm panels)
- Changing materials (GI pipe to MS pipe, etc.)
- Updating project fields (name, client, location, building type, hazard category)
- Removing or adding BOQ sections/items related to fire safety
- Changing fire standards (NBC, NFPA)
- Regenerating the BOQ
- Text replacements in descriptions

INVALID instructions include:
- Adding non-fire items (food, furniture, clothing, office supplies, vegetables, fruits, etc.)
- Nonsensical units for fire equipment (e.g., "2 kg smoke detectors", "3 liters of alarm panels")
- Completely unrelated items (e.g., "add tomatoes", "install sofa", "buy laptop")
- Gibberish or random text

SUSPICIOUS (valid but unusual) instructions include:
- Very high quantities (e.g., 10000 smoke detectors for a building)
- Unknown brand names or materials that might be intentional
- Custom items that are not standard fire equipment but could be valid additions

Respond with ONLY this JSON:
{{
  "valid": true or false,
  "suspicious": true or false,
  "reason": "brief explanation if not valid or suspicious",
  "suggestion": "what the user should do instead, if applicable"
}}

Rules:
- If completely unrelated to fire BOQ: valid=false, suspicious=false
- If borderline/unusual but possible: valid=true, suspicious=true
- If perfectly normal fire BOQ instruction: valid=true, suspicious=false
- Return ONLY JSON, no explanation"""


async def _validate_instruction(instruction: str, project: dict, ai_model: str) -> dict:
    """
    Validate if the instruction is appropriate for a fire BOQ.
    Returns: { "valid": bool, "suspicious": bool, "reason": str, "suggestion": str }
    """
    prompt = _build_validation_prompt(instruction, project)
    try:
        result = await _call_ai_model(prompt, ai_model)
        if result and isinstance(result, dict):
            return result
    except Exception as e:
        print(f"[AGENT] Validation error: {e}")
    # If validation itself fails, allow the instruction (fail open)
    return {"valid": True, "suspicious": False, "reason": "", "suggestion": ""}


# ── Intent detection + BOQ modification prompt ────────────────────────────────
def _build_agent_prompt(instruction: str, current_boq: dict, project: dict) -> str:
    sections_summary = []
    for sec in current_boq.get("sections", []):
        items_info = []
        for item in sec.get("items", []):
            items_info.append({
                "sno": item.get("sno"),
                "item": item.get("item"),
                "description": item.get("description", "")[:120],  # truncated for context
                "quantity": item.get("quantity"),
                "unit": item.get("unit"),
            })
        sections_summary.append({
            "section_id": sec.get("section_id"),
            "section_name": sec.get("section_name"),
            "items": items_info,
        })

    current_standard = current_boq.get("standard", "NBC")

    # Build project info summary
    project_info = {
        "project_name": project.get("project_name", ""),
        "client_name": project.get("client_name", ""),
        "building_type": project.get("building_type", ""),
        "hazard_category": project.get("hazard_category", ""),
        "location": project.get("location", ""),
        "fire_standard": project.get("fire_standard", current_standard),
    }

    return f"""You are a Fire Project & BOQ (Bill of Quantities) modification AI agent.
You will receive a user instruction and the current project + BOQ structure, then output ONLY a valid JSON modification plan.

Current Project Info:
{json.dumps(project_info, indent=2)}

Current BOQ Standard: {current_standard}
Current BOQ Sections:
{json.dumps(sections_summary, indent=2)}

User Instruction: "{instruction}"

Analyze the instruction and return a JSON plan in EXACTLY this format:
{{
  "intent": "<one of: update_quantity | remove_section | add_item | change_standard | regenerate_boq | custom_update | update_project>",
  "summary": "<one line human-readable summary of what will be done>",
  "project_updates": {{}},
  "changes": [
    {{
      "action": "<one of: set_quantity | remove_section | add_item | update_item | remove_item | replace_text>",
      "section_id": "<section ID like A, B, C — use * to apply across ALL sections>",
      "section_name": "<section name if needed>",
      "item_sno": null,
      "item_keyword": "<keyword to match item name/description, or null — use null to apply to ALL items in section>",
      "new_quantity": null,
      "find_text": "<text to find and replace — for replace_text action only>",
      "replace_text": "<replacement text — for replace_text action only>",
      "new_item": null
    }}
  ],
  "new_standard": null,
  "requires_regeneration": false
}}

Rules:
- For project field updates (name, client, building type, location, hazard): set intent="update_project", project_updates={{"project_name": "new name"}} (only include fields that need to change)
  - Editable project fields: project_name, client_name, building_type, hazard_category (light/ordinary/high/extra_high), location
  - Leave changes=[] when updating project fields only
- For quantity updates: use "set_quantity" action with section_id + item_keyword (partial match OK) + new_quantity as a number
- For removing a section: use "remove_section" action with section_id
- For adding an item: use "add_item" action with section_id + new_item object containing sno, item, description, unit, quantity, calculation_basis
- For text/material replacement across items (e.g. 'GI se MS karo', 'replace GI pipe with MS C class'): use "replace_text" action with:
  - section_id: "*" to apply to ALL sections, or specific section ID
  - item_keyword: null (to match all items) or keyword to filter specific items
  - find_text: the exact text to find (e.g. "GI", "Galvanized Iron")
  - replace_text: the replacement text (e.g. "MS C Class", "MS B Class")
  - This replaces text in BOTH the 'item' name AND 'description' fields
  - Use multiple replace_text changes if multiple variants exist (e.g. "GI" and "Galvanised Iron")
- For standard change (NBC/NFPA): set intent="change_standard", new_standard="NBC" or "NFPA", requires_regeneration=true
- For full BOQ regeneration: set intent="regenerate_boq", requires_regeneration=true
- If instruction is in Hindi/Hinglish, translate and apply correctly
- "project name Abc se ABCCC kr" means update_project with project_name="ABCCC"
- "GI se MS C class karo" means replace_text with find_text="GI", replace_text="MS C Class", section_id="*"
- Return ONLY the JSON, no explanation, no markdown fences"""


# ── Apply modifications to BOQ sections ───────────────────────────────────────
def _apply_changes(sections: list, changes: list) -> tuple:
    """Apply AI-determined changes to BOQ sections. Returns (updated_sections, applied_log)."""
    applied_log = []

    for change in changes:
        action = change.get("action", "")
        section_id = change.get("section_id", "")
        item_keyword = (change.get("item_keyword") or "").lower().strip()
        item_sno = change.get("item_sno")
        new_quantity = change.get("new_quantity")
        new_item = change.get("new_item")

        if action == "remove_section":
            before_count = len(sections)
            sections = [s for s in sections if s.get("section_id") != section_id]
            if len(sections) < before_count:
                applied_log.append(f"Removed section '{section_id}'")

        elif action in ("set_quantity", "update_item"):
            for sec in sections:
                if sec.get("section_id") == section_id or section_id == "*":
                    for item in sec.get("items", []):
                        match = False
                        if item_keyword and item_keyword in item.get("item", "").lower():
                            match = True
                        elif item_sno is not None and item.get("sno") == item_sno:
                            match = True
                        if match and new_quantity is not None:
                            old_qty = item.get("quantity")
                            item["quantity"] = float(new_quantity)
                            applied_log.append(
                                f"Updated '{item['item']}': {old_qty} → {new_quantity} {item.get('unit','')}"
                            )

        elif action == "remove_item":
            for sec in sections:
                if sec.get("section_id") == section_id:
                    before = len(sec.get("items", []))
                    sec["items"] = [
                        it for it in sec.get("items", [])
                        if not (item_keyword and item_keyword in it.get("item", "").lower())
                        and not (item_sno is not None and it.get("sno") == item_sno)
                    ]
                    removed = before - len(sec.get("items", []))
                    if removed > 0:
                        applied_log.append(f"Removed {removed} item(s) from section '{section_id}'")

        elif action == "add_item" and new_item:
            for sec in sections:
                if sec.get("section_id") == section_id:
                    if not isinstance(sec.get("items"), list):
                        sec["items"] = []
                    max_sno = max((it.get("sno", 0) for it in sec["items"]), default=0)
                    new_item["sno"] = max_sno + 1
                    sec["items"].append(new_item)
                    applied_log.append(
                        f"Added '{new_item.get('item', 'New Item')}' to section '{section_id}'"
                    )

        elif action == "replace_text":
            # Text replacement in item name and description across matching sections/items
            find_str = (change.get("find_text") or "").strip()
            repl_str = (change.get("replace_text") or "").strip()
            if not find_str or not repl_str:
                applied_log.append("replace_text: missing find_text or replace_text")
                continue
            find_lower = find_str.lower()
            count = 0
            for sec in sections:
                # section filter: "*" means all sections
                if section_id != "*" and sec.get("section_id") != section_id:
                    continue
                for item in sec.get("items", []):
                    # item_keyword filter: if set, only match items containing the keyword
                    if item_keyword:
                        combined = (
                            item.get("item", "") + " " + item.get("description", "")
                        ).lower()
                        if item_keyword not in combined:
                            continue
                    changed = False
                    # Replace in item name (case-insensitive)
                    old_name = item.get("item", "")
                    new_name = _case_insensitive_replace(old_name, find_str, repl_str)
                    if new_name != old_name:
                        item["item"] = new_name
                        changed = True
                    # Replace in description (case-insensitive)
                    old_desc = item.get("description", "")
                    new_desc = _case_insensitive_replace(old_desc, find_str, repl_str)
                    if new_desc != old_desc:
                        item["description"] = new_desc
                        changed = True
                    if changed:
                        count += 1
            if count > 0:
                applied_log.append(
                    f"Replaced '{find_str}' → '{repl_str}' in {count} item(s)"
                )
            else:
                applied_log.append(
                    f"No items contained '{find_str}' — nothing replaced"
                )

    return sections, applied_log


# ── Main agent entry point ─────────────────────────────────────────────────────
async def execute_agent_instruction(
    instruction: str,
    project_id: str,
    project: dict,
    current_boq: dict,
    ai_model: str = "groq",
    confirmed: bool = False,
) -> dict:
    """
    Parse the user's natural-language instruction and apply modifications to the BOQ
    or project fields. Uses the project's configured AI model (default: Groq).

    If confirmed=False, first validates the instruction. If suspicious or invalid,
    returns needs_confirmation=True with a message for the user to confirm.
    If confirmed=True, skips validation and executes directly.

    Returns result with: success, summary, applied_changes, updated_sections,
    requires_regeneration, new_standard, intent, project_updates,
    needs_confirmation, confirmation_message, confirmation_type.
    """

    # ── Phase 1: Validate instruction (skip if user already confirmed) ──────────
    if not confirmed:
        validation = await _validate_instruction(instruction, project, ai_model)
        is_valid = validation.get("valid", True)
        is_suspicious = validation.get("suspicious", False)
        reason = validation.get("reason", "")
        suggestion = validation.get("suggestion", "")

        if not is_valid:
            # Completely invalid / non-BOQ — ask for confirmation with clear AI opinion message
            msg = (
                "🔍 **AI BOQ Domain Analysis**:\n"
                "Yeh instruction Fire Safety BOQ Standards ke mutabiq standard/valid nahi lag rahi hai.\n\n"
                f"• **Analysis**: {reason or 'Requested item/change does not conform to standard fire protection specifications.'}\n"
                f"• **Suggestion**: {suggestion or 'Kripya standard Fire BOQ items, quantities, or project fields enter karein.'}\n\n"
                "Kya aap fir bhi ise BOQ me **Proceed / Force Execute** karna chahte hain?"
            )
            return {
                "success": False,
                "summary": "Invalid/Non-BOQ instruction detected",
                "applied_changes": [],
                "updated_sections": current_boq.get("sections", []),
                "requires_regeneration": False,
                "new_standard": None,
                "intent": "unknown",
                "project_updates": {},
                "needs_confirmation": True,
                "confirmation_message": msg,
                "confirmation_type": "invalid",
            }

        if is_suspicious:
            # Unusual but possible — ask user to confirm before proceeding
            msg = (
                "⚠️ **AI Engineering Analysis**:\n"
                "Yeh instruction Fire BOQ specifications ke mutabiq thodi unusual lag rahi hai.\n\n"
                f"• **Analysis**: {reason or 'Noticeable deviation from standard BOQ patterns.'}\n\n"
                "Kya aap sure hain ki ise BOQ me **Proceed / Execute** karna chahte hain?"
            )
            return {
                "success": False,
                "summary": "Unusual instruction — confirmation required",
                "applied_changes": [],
                "updated_sections": current_boq.get("sections", []),
                "requires_regeneration": False,
                "new_standard": None,
                "intent": "unknown",
                "project_updates": {},
                "needs_confirmation": True,
                "confirmation_message": msg,
                "confirmation_type": "suspicious",
            }

    # ── Phase 2: Build prompt and call the correct AI model ─────────────────────
    prompt = _build_agent_prompt(instruction, current_boq, project)
    plan = await _call_ai_model(prompt, ai_model)

    if not plan:
        return {
            "success": False,
            "summary": "AI could not parse your instruction. Please try being more specific.",
            "applied_changes": [],
            "updated_sections": current_boq.get("sections", []),
            "requires_regeneration": False,
            "new_standard": None,
            "intent": "unknown",
            "project_updates": {},
            "needs_confirmation": False,
            "confirmation_message": None,
            "confirmation_type": None,
        }

    intent = plan.get("intent", "custom_update")
    summary = plan.get("summary", "Updated")
    changes = plan.get("changes", [])
    requires_regen = plan.get("requires_regeneration", False)
    new_standard = plan.get("new_standard")
    project_updates = plan.get("project_updates") or {}

    # ── Handle project field updates (name, client, location, etc.) ───────────
    if intent == "update_project" and project_updates:
        # Sanitize — only allow known safe fields
        ALLOWED_PROJECT_FIELDS = {
            "project_name", "client_name", "building_type",
            "hazard_category", "location", "fire_standard",
        }
        safe_updates = {
            k: v for k, v in project_updates.items()
            if k in ALLOWED_PROJECT_FIELDS and isinstance(v, str) and v.strip()
        }
        if safe_updates:
            applied_log = [
                f"'{k}' changed to '{v}'" for k, v in safe_updates.items()
            ]
            return {
                "success": True,
                "summary": summary,
                "applied_changes": applied_log,
                "updated_sections": current_boq.get("sections", []),
                "requires_regeneration": False,
                "new_standard": None,
                "intent": intent,
                "project_updates": safe_updates,
                "needs_confirmation": False,
                "confirmation_message": None,
                "confirmation_type": None,
            }
        else:
            return {
                "success": False,
                "summary": "No valid project fields to update.",
                "applied_changes": [],
                "updated_sections": current_boq.get("sections", []),
                "requires_regeneration": False,
                "new_standard": None,
                "intent": intent,
                "project_updates": {},
                "needs_confirmation": False,
                "confirmation_message": None,
                "confirmation_type": None,
            }

    # ── Handle BOQ regeneration ────────────────────────────────────────────────
    if requires_regen:
        return {
            "success": True,
            "summary": summary,
            "applied_changes": [
                f"BOQ will be regenerated with {new_standard or 'current'} standard"
            ],
            "updated_sections": current_boq.get("sections", []),
            "requires_regeneration": True,
            "new_standard": new_standard,
            "intent": intent,
            "project_updates": {},
            "needs_confirmation": False,
            "confirmation_message": None,
            "confirmation_type": None,
        }

    # ── Apply targeted BOQ section/item changes ────────────────────────────────
    current_sections = [s.copy() for s in current_boq.get("sections", [])]
    for sec in current_sections:
        sec["items"] = [dict(it) for it in sec.get("items", [])]

    updated_sections, applied_log = _apply_changes(current_sections, changes)

    if not applied_log:
        applied_log = [
            "No matching items found. Check section IDs and item names in your BOQ."
        ]

    return {
        "success": True,
        "summary": summary,
        "applied_changes": applied_log,
        "updated_sections": updated_sections,
        "requires_regeneration": False,
        "new_standard": None,
        "intent": intent,
        "project_updates": {},
        "needs_confirmation": False,
        "confirmation_message": None,
        "confirmation_type": None,
    }
