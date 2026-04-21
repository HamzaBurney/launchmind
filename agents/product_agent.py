"""
agents/product_agent.py
Product Agent - generates a structured product specification.

Inputs : task message from CEO (in bus inbox)
Outputs: product spec JSON → Engineer & Marketing agents
         confirmation → CEO
"""

import json
import re

from agents.llm_client import generate_text
from message_bus import bus

def _llm(system: str, user: str, max_tokens: int = 2000) -> str:
    return generate_text(system=system, user=user, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Core spec generation
# ---------------------------------------------------------------------------
SPEC_SYSTEM = """
You are a senior product manager. Given a startup idea, produce a thorough product specification.
Return ONLY a valid JSON object (no markdown, no fences) with this exact structure:

{
  "value_proposition": "One sentence describing what the product does and for whom",
  "personas": [
    {"name": "...", "role": "...", "pain_point": "..."},
    {"name": "...", "role": "...", "pain_point": "..."},
    {"name": "...", "role": "...", "pain_point": "..."}
  ],
  "features": [
    {"name": "...", "description": "...", "priority": 1},
    {"name": "...", "description": "...", "priority": 2},
    {"name": "...", "description": "...", "priority": 3},
    {"name": "...", "description": "...", "priority": 4},
    {"name": "...", "description": "...", "priority": 5}
  ],
  "user_stories": [
    {"as_a": "...", "i_want": "...", "so_that": "..."},
    {"as_a": "...", "i_want": "...", "so_that": "..."},
    {"as_a": "...", "i_want": "...", "so_that": "..."}
  ]
}

Be specific. Use real names for personas. Make features concrete and ranked.
"""


def _parse_json(raw: str) -> dict:
    candidates = [raw]

    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
        candidates.append(stripped)

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        candidates.append(match.group())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    return {
        "value_proposition": "A product that solves a real customer problem.",
        "personas": [
            {"name": "Alex", "role": "User", "pain_point": "Needs a faster workflow"},
            {"name": "Jordan", "role": "User", "pain_point": "Wants reliable results"},
            {"name": "Taylor", "role": "User", "pain_point": "Needs easy onboarding"},
        ],
        "features": [
            {"name": "Core workflow", "description": "Supports the primary user journey", "priority": 1},
            {"name": "Search", "description": "Find relevant information quickly", "priority": 2},
            {"name": "Notifications", "description": "Keep users informed", "priority": 3},
            {"name": "Reporting", "description": "Provide useful summaries", "priority": 4},
            {"name": "Settings", "description": "Allow user customization", "priority": 5},
        ],
        "user_stories": [
            {"as_a": "user", "i_want": "to complete tasks quickly", "so_that": "I can save time"},
            {"as_a": "user", "i_want": "clear guidance", "so_that": "I can avoid mistakes"},
            {"as_a": "user", "i_want": "reliable output", "so_that": "I can trust the product"},
        ],
    }


def generate_spec(idea: str, focus: str, feedback: str = "") -> dict:
    feedback_section = f"\nAdditional feedback to address:\n{feedback}" if feedback else ""
    user_prompt = (
        f"Startup idea: {idea}\n"
        f"Focus areas: {focus}"
        f"{feedback_section}"
    )
    raw = _llm(SPEC_SYSTEM, user_prompt, max_tokens=2500)
    spec = _parse_json(raw)
    spec.setdefault("value_proposition", "A product that solves a real customer problem.")
    spec.setdefault("personas", [])
    spec.setdefault("features", [])
    spec.setdefault("user_stories", [])
    print(f"[PRODUCT] Generated spec: {spec.get('value_proposition', '')[:80]}…")
    return spec


# ---------------------------------------------------------------------------
# Agent run loop
# ---------------------------------------------------------------------------
def run():
    print("\n[PRODUCT] Agent starting…")
    messages = bus.receive("product")

    if not messages:
        print("[PRODUCT] No messages in inbox - nothing to do.")
        return

    # Process the latest task or revision_request
    msg = messages[-1]
    payload = msg["payload"]
    idea = payload.get("idea", "")
    focus = payload.get("focus", "")
    feedback = payload.get("feedback", "") if msg["message_type"] == "revision_request" else ""

    print(f"[PRODUCT] Processing message type={msg['message_type']} from {msg['from_agent']}")

    spec = generate_spec(idea, focus, feedback)

    # Send spec to Engineer agent
    bus.send(
        from_agent="product",
        to_agent="engineer",
        message_type="result",
        payload={"spec": spec},
        parent_message_id=msg["message_id"],
    )

    # Send spec to Marketing agent
    bus.send(
        from_agent="product",
        to_agent="marketing",
        message_type="result",
        payload={"spec": spec},
        parent_message_id=msg["message_id"],
    )

    # Send confirmation to CEO
    bus.send(
        from_agent="product",
        to_agent="ceo",
        message_type="confirmation",
        payload={
            "status": "spec_ready",
            "spec": spec,
            "message": f"Product spec complete. Value prop: {spec.get('value_proposition', '')}",
        },
        parent_message_id=msg["message_id"],
    )
    print("[PRODUCT] Spec dispatched to engineer, marketing, and ceo.")
