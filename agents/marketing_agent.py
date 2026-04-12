"""
agents/marketing_agent.py
Marketing Agent – generates copy, sends a real email, posts to Slack.

Real actions:
    - Sends email via Gmail API
  - Posts Slack message using Block Kit
"""

import json
import os
import re

import requests

from agents.gmail_client import send_email_via_gmail
from agents.llm_client import generate_text
from message_bus import bus

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "agent@launchmind.ai")
EMAIL_TO = os.environ.get("EMAIL_TO", "")  # test inbox


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------
def _llm(system: str, user: str, max_tokens: int = 2000) -> str:
    return generate_text(system=system, user=user, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Copy generation
# ---------------------------------------------------------------------------
COPY_SYSTEM = """
You are a world-class growth marketer specializing in high-converting outreach.
Given a product specification, produce marketing copy.
Return ONLY a valid JSON object (no markdown, no fences) with this exact structure:

{
  "tagline": "Under 10 words – punchy and memorable",
  "short_description": "2-3 sentences suitable for a landing page hero section",
  "email": {
        "subject": "Compelling benefit-driven subject line (20-70 chars)",
        "body_sections": {
            "greeting": "Hi,",
            "hook": "1-2 sentences that clearly identify the persona pain point",
            "solution": "2-3 sentences explaining how the product solves the pain point with specific features",
            "benefits": [
                "Benefit 1",
                "Benefit 2",
                "Benefit 3"
            ],
            "cta_primary": "One direct action CTA",
            "cta_secondary": "Optional secondary action",
            "sign_off": "Best regards,\\nLaunchMind Team"
        },
        "body": "Plain-text email assembled from sections, 150-250 words, with clear CTA"
  },
  "social": {
    "twitter": "Tweet under 280 chars with relevant hashtags",
    "linkedin": "Professional LinkedIn post 2-3 paragraphs",
    "instagram": "Instagram caption with emojis and hashtags"
  }
}

Quality constraints:
- Include at least one placeholder such as {{recipient_name}} or {{persona_role}}
- Include at least one strong CTA action word (book, start, join, claim, schedule, try)
- Mention at least 2 concrete product features in the email solution/benefits
"""


def _render_email_body(email_obj: dict) -> str:
    sections = email_obj.get("body_sections", {}) or {}
    benefits = sections.get("benefits", []) or []

    if not isinstance(benefits, list):
        benefits = [str(benefits)]

    benefits_lines = "\n".join(f"- {b}" for b in benefits if str(b).strip())

    parts = [
        sections.get("greeting", "Hi {{recipient_name}},").strip(),
        "",
        sections.get("hook", "Finding a reliable solution should not be this hard.").strip(),
        "",
        sections.get("solution", "We built a focused product to solve this with speed and clarity.").strip(),
        "",
    ]
    if benefits_lines:
        parts.extend(["Key benefits:", benefits_lines, ""])

    parts.extend(
        [
            sections.get("cta_primary", "Claim early access today.").strip(),
            sections.get("cta_secondary", "Reply to this email to schedule a quick walkthrough.").strip(),
            "",
            sections.get("sign_off", "Best regards,\nLaunchMind Team").strip(),
        ]
    )
    return "\n".join(parts).strip()


def _validate_email_structure(copy: dict) -> list[str]:
    warnings: list[str] = []
    email = copy.get("email", {}) or {}
    subject = str(email.get("subject", "")).strip()
    body = str(email.get("body", "")).strip()
    sections_text = json.dumps(email.get("body_sections", {}), ensure_ascii=False)

    if len(subject) < 20:
        warnings.append("Subject is too short; target at least 20 characters.")
    if len(subject) > 70:
        warnings.append("Subject is too long; keep under 70 characters.")

    body_word_count = len(body.split())
    if body_word_count < 80:
        warnings.append("Email body is too short; add clearer problem, solution, and CTA details.")
    if body_word_count > 320:
        warnings.append("Email body is too long; keep it concise and scannable.")

    cta_keywords = ("book", "start", "join", "claim", "schedule", "try", "reply", "access")
    if not any(k in body.lower() for k in cta_keywords):
        warnings.append("Email body is missing a clear CTA action.")

    placeholder_pool = f"{subject}\n{body}\n{sections_text}"
    if "{{" not in placeholder_pool or "}}" not in placeholder_pool:
        warnings.append("Email lacks personalization placeholders like {{recipient_name}}.")

    return warnings


def _build_fallback_copy(reason: str = "") -> dict:
    fallback = {
        "tagline": "Find Vet Care Fast",
        "short_description": (
            "A mobile app for dog owners to find nearby vets with real-time availability, "
            "book quickly, and avoid stressful emergency searches."
        ),
        "email": {
            "subject": "Emergency vet help in minutes, not hours",
            "body_sections": {
                "greeting": "Hi {{recipient_name}},",
                "hook": (
                    "When a dog needs urgent care, owners waste valuable time calling clinic after clinic "
                    "just to find an available vet."
                ),
                "solution": (
                    "Our app shows nearby vets with real-time availability, verified reviews, and instant "
                    "appointment booking in one place."
                ),
                "benefits": [
                    "Find available vets in seconds instead of endless calls",
                    "Book immediately with transparent availability",
                    "Get peace of mind during urgent pet health moments",
                ],
                "cta_primary": "Claim early access now and test it first.",
                "cta_secondary": "Reply to this email to schedule a short demo.",
                "sign_off": "Best regards,\nLaunchMind Team",
            },
        },
        "social": {
            "twitter": "Find available vets in minutes, not hours. #PetCare #VetTech",
            "linkedin": "We are helping dog owners find nearby vets with real-time availability and instant booking.",
            "instagram": "Vet care, faster and easier for dog owners. #PetCare",
        },
    }
    fallback["email"]["body"] = _render_email_body(fallback["email"])
    if reason:
        fallback["quality_notice"] = reason
    return fallback


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

    return {}


def generate_copy(spec: dict, feedback: str = "") -> dict:
    feedback_section = f"\nRevision feedback:\n{feedback}" if feedback else ""
    user_prompt = (
        f"Value proposition: {spec.get('value_proposition', '')}\n"
        f"Target personas: {json.dumps(spec.get('personas', []))}\n"
        f"Core features: {json.dumps(spec.get('features', []))}\n"
        f"User stories: {json.dumps(spec.get('user_stories', []))}"
        f"{feedback_section}"
    )
    raw = _llm(COPY_SYSTEM, user_prompt, max_tokens=2000)
    copy = _parse_json(raw)

    if not copy:
        copy = _build_fallback_copy("LLM output was not valid JSON.")

    copy.setdefault("tagline", "Launching soon")
    copy.setdefault("short_description", "A new product is launching soon.")
    copy.setdefault("email", {})
    copy["email"].setdefault("subject", "New launch announcement with early access")
    copy["email"].setdefault(
        "body_sections",
        {
            "greeting": "Hi {{recipient_name}},",
            "hook": "We are launching a product that solves a painful workflow for your team.",
            "solution": "It provides a faster and clearer way to get results with less manual work.",
            "benefits": ["Save time", "Reduce stress", "Improve outcomes"],
            "cta_primary": "Join early access today.",
            "cta_secondary": "Reply to request a walkthrough.",
            "sign_off": "Best regards,\nLaunchMind Team",
        },
    )
    if not copy["email"].get("body"):
        copy["email"]["body"] = _render_email_body(copy["email"])

    copy.setdefault("social", {})
    copy["social"].setdefault("twitter", "Launching soon. Stay tuned.")
    copy["social"].setdefault("linkedin", "We are preparing a new product launch. Stay tuned for updates.")
    copy["social"].setdefault("instagram", "Launching soon. Stay tuned.")

    quality_warnings = _validate_email_structure(copy)
    copy["quality_warnings"] = quality_warnings
    copy["qa_quality_score"] = max(0, 100 - (15 * len(quality_warnings)))

    if quality_warnings:
        print(f"[MARKETING] Email quality warnings: {quality_warnings}")

    print(f"[MARKETING] Generated tagline: {copy.get('tagline', '')}")
    return copy


# ---------------------------------------------------------------------------
# Email via Gmail API
# ---------------------------------------------------------------------------
def send_email(subject: str, body: str, to_email: str) -> bool:
    if not to_email:
        print("[MARKETING] EMAIL_TO not set – skipping email send.")
        return False

    return send_email_via_gmail(
        subject=subject,
        body=body,
        to_email=to_email,
        from_email=EMAIL_FROM,
    )


# ---------------------------------------------------------------------------
# Slack Block Kit posting (shared with CEO agent)
# ---------------------------------------------------------------------------
def post_slack_block_kit(blocks: list, channel: str = "#launches") -> bool:
    if not SLACK_BOT_TOKEN:
        print("[MARKETING] SLACK_BOT_TOKEN not set – skipping Slack post.")
        return False
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"channel": channel, "blocks": blocks},
            timeout=15,
        )
        data = resp.json()
        if data.get("ok"):
            print(f"[MARKETING] Slack message posted to {channel}")
            return True
        else:
            print(f"[MARKETING] Slack error: {data.get('error', 'unknown')}")
            return False
    except Exception as e:
        print(f"[MARKETING] Slack post failed: {e}")
        return False


def _build_launch_blocks(tagline: str, short_description: str, pr_url: str) -> list:
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🚀 New Launch: {tagline}", "emoji": True},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": short_description},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*GitHub PR:* <{pr_url}|View Pull Request>"},
                {"type": "mrkdwn", "text": "*Status:* 🟢 Ready for review"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "Posted by LaunchMind Marketing Agent 🤖"}
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Agent run loop
# ---------------------------------------------------------------------------
def run():
    print("\n[MARKETING] Agent starting…")
    messages = bus.receive("marketing")

    if not messages:
        print("[MARKETING] No messages in inbox – nothing to do.")
        return

    # Collect the latest task/revision from CEO (may have multiple messages – take last)
    relevant = [m for m in messages if m["message_type"] in ("task", "revision_request", "result")]
    if not relevant:
        print("[MARKETING] No relevant messages.")
        return

    msg = relevant[-1]
    payload = msg["payload"]
    spec = payload.get("spec", {})
    pr_url = payload.get("pr_url", "#")
    feedback = payload.get("feedback", "") if msg["message_type"] == "revision_request" else ""
    mode = str(payload.get("mode", "publish")).strip().lower()
    should_publish = mode == "publish"

    print(f"[MARKETING] Processing: type={msg['message_type']} mode={mode}")

    # 1. Generate copy
    draft_copy = payload.get("draft_copy")
    if should_publish and isinstance(draft_copy, dict) and draft_copy:
        copy = draft_copy
        print("[MARKETING] Using approved draft copy for one-time publish.")
    else:
        copy = generate_copy(spec, feedback)

    email_sent = False
    slack_sent = False

    if should_publish:
        # 2. Send email
        email_sent = send_email(
            subject=copy["email"]["subject"],
            body=copy["email"]["body"],
            to_email=EMAIL_TO,
        )

        # 3. Post to Slack
        blocks = _build_launch_blocks(
            tagline=copy.get("tagline", ""),
            short_description=copy.get("short_description", ""),
            pr_url=pr_url,
        )
        slack_sent = post_slack_block_kit(blocks, channel="#launches")
    else:
        print("[MARKETING] Draft mode – skipping email and Slack publishing.")

    # 4. Report back to CEO
    bus.send(
        from_agent="marketing",
        to_agent="ceo",
        message_type="result",
        payload={
            **copy,
            "email_sent": email_sent,
            "slack_sent": slack_sent,
            "pr_url": pr_url,
            "mode": mode,
            "published": should_publish and (email_sent or slack_sent),
        },
        parent_message_id=msg["message_id"],
    )
    print("[MARKETING] Done.")
