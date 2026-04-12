"""
agents/ceo_agent.py
CEO Agent – Orchestrator of the LaunchMind MAS.

Responsibilities:
 1. Receive the startup idea.
 2. Use LLM to decompose it into per-agent tasks.
 3. Dispatch tasks via the message bus.
 4. Wait for results from each sub-agent.
 5. Use LLM to review each result; send revision_request if needed.
 6. Compile and post a final Slack summary.
 7. Maintain a full decision log.
"""

import json
import os
from datetime import datetime, timezone

from agents.llm_client import generate_text
from message_bus import bus

def _llm(system: str, user: str, max_tokens: int = 2000) -> str:
    return generate_text(system=system, user=user, max_tokens=max_tokens)


def _extract_first_json_object(text: str) -> str | None:
    """Extract the first balanced JSON object from arbitrary text."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return None


def _safe_parse_json_object(raw: str, fallback: dict) -> dict:
    """Best-effort JSON parse for model outputs; never raises."""
    candidates = [raw]

    # Common case: model wraps response in markdown code fences.
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
        candidates.append(stripped)

    extracted = _extract_first_json_object(raw)
    if extracted:
        candidates.append(extracted)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    return fallback


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------
_decision_log: list[dict] = []


def _log(decision: str, reasoning: str, action: str):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "reasoning": reasoning,
        "action": action,
    }
    _decision_log.append(entry)
    print(f"\n[CEO LOG] {decision}\n  Reasoning: {reasoning}\n  Action: {action}")
    return entry


def get_decision_log():
    return list(_decision_log)


# ---------------------------------------------------------------------------
# Step 1 – Decompose the idea into agent tasks
# ---------------------------------------------------------------------------
def _decompose_idea(idea: str) -> dict:
    system = (
        "You are the CEO of a startup incubator. You receive raw startup ideas and break "
        "them into concrete tasks for three specialist agents: product, engineer, marketing. "
        "Return ONLY a valid JSON object – no markdown fences, no extra text – with exactly "
        "three keys: 'product_task', 'engineer_task', 'marketing_task'. Each value is a "
        "plain-string instruction addressed to that agent."
    )
    user = f"Startup idea: {idea}\n\nDecompose this into tasks for the three agents."
    raw = _llm(system, user)
    tasks = _safe_parse_json_object(
        raw,
        {
            "product_task": "Define a concrete product specification from the startup idea with personas, prioritized features, and user stories.",
            "engineer_task": "Build a polished single-page landing page that reflects the value proposition and key features.",
            "marketing_task": "Create launch-ready marketing copy including tagline, outreach email, and social posts.",
        },
    )

    # Ensure required keys exist even when model output is partial.
    tasks.setdefault(
        "product_task",
        "Define a concrete product specification from the startup idea with personas, prioritized features, and user stories.",
    )
    tasks.setdefault(
        "engineer_task",
        "Build a polished single-page landing page that reflects the value proposition and key features.",
    )
    tasks.setdefault(
        "marketing_task",
        "Create launch-ready marketing copy including tagline, outreach email, and social posts.",
    )
    _log(
        "Decomposed startup idea into agent tasks",
        f"Idea requires product definition, technical build, and marketing – "
        f"each handled by a specialist agent.",
        f"Sent tasks: product='{tasks['product_task'][:60]}…'",
    )
    return tasks


# ---------------------------------------------------------------------------
# Step 2 – Review an agent's output
# ---------------------------------------------------------------------------
def _review_output(agent_name: str, output: dict, spec_context: str) -> tuple[bool, str]:
    """
    Returns (is_acceptable: bool, feedback: str).
    Uses LLM to reason about quality.
    """
    system = (
        "You are a demanding but fair CEO. You review work produced by your agents. "
        "Reply ONLY with a JSON object with two keys: "
        "'acceptable' (boolean) and 'feedback' (string). "
        "No markdown, no extra text."
    )
    user = (
        f"Agent reviewed: {agent_name}\n"
        f"Context / spec: {spec_context}\n"
        f"Agent output:\n{json.dumps(output, indent=2)}\n\n"
        "Is this output specific, complete, and actionable? "
        "If not, what exactly is missing or weak?"
    )
    raw = _llm(system, user)
    verdict = _safe_parse_json_object(
        raw,
        {"acceptable": True, "feedback": "Parse error; accepting by default."},
    )

    acceptable = bool(verdict.get("acceptable", True))
    feedback = str(verdict.get("feedback", ""))
    _log(
        f"Reviewed {agent_name} output",
        feedback,
        "Accepted" if acceptable else f"Sent revision_request to {agent_name}",
    )
    return acceptable, feedback


# ---------------------------------------------------------------------------
# Step 3 – Post final Slack summary
# ---------------------------------------------------------------------------
def _post_final_slack_summary(idea: str, pr_url: str, issue_url: str, marketing_copy: dict):
    """Post a final CEO summary to Slack via the Marketing agent's helper."""
    try:
        from agents.marketing_agent import post_slack_block_kit

        tagline = marketing_copy.get("tagline", "Launching something amazing")
        description = marketing_copy.get("short_description", "")

        summary_blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🚀 LaunchMind CEO Report"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Startup Idea:* {idea}\n*Tagline:* _{tagline}_\n{description}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*GitHub PR:* <{pr_url}|View PR>"},
                    {"type": "mrkdwn", "text": f"*GitHub Issue:* <{issue_url}|View Issue>"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "All agents completed successfully ✅ | LaunchMind MAS",
                    }
                ],
            },
        ]
        post_slack_block_kit(summary_blocks, channel="#launches")
        _log("Posted final CEO summary to Slack", "All agents complete", "Slack message sent")
    except Exception as e:
        print(f"[CEO] Warning: Could not post final Slack summary: {e}")


# ---------------------------------------------------------------------------
# Main orchestration entry point
# ---------------------------------------------------------------------------
def run(idea: str, max_revisions: int = 2):
    """
    Full CEO orchestration lifecycle.
    Returns a dict with all collected outputs.
    """
    print("\n" + "=" * 60)
    print("CEO AGENT STARTING")
    print("=" * 60)
    print(f"Startup idea: {idea}\n")

    # ── 1. Decompose idea ──────────────────────────────────────────────
    tasks = _decompose_idea(idea)

    # ── 2. Send task to Product agent ──────────────────────────────────
    bus.send(
        from_agent="ceo",
        to_agent="product",
        message_type="task",
        payload={"idea": idea, "focus": tasks["product_task"]},
    )

    # ── 3. Run Product agent ───────────────────────────────────────────
    from agents.product_agent import run as run_product

    product_spec = None
    for attempt in range(1, max_revisions + 2):
        print(f"\n[CEO] Running Product agent (attempt {attempt})…")
        run_product()
        product_messages = bus.receive("ceo")
        product_result = next(
            (m for m in product_messages if m["from_agent"] == "product" and m["message_type"] in ("result", "confirmation")),
            None,
        )
        if not product_result:
            print("[CEO] No result from Product agent – retrying…")
            continue

        product_spec = product_result["payload"].get("spec") or product_result["payload"]
        acceptable, feedback = _review_output("product", product_spec, idea)

        if acceptable or attempt > max_revisions:
            break

        # Send revision request
        bus.send(
            from_agent="ceo",
            to_agent="product",
            message_type="revision_request",
            payload={"feedback": feedback, "idea": idea, "focus": tasks["product_task"]},
            parent_message_id=product_result["message_id"],
        )

    if not product_spec:
        raise RuntimeError("Product agent failed to deliver an acceptable spec.")

    # ── 4. Draft loop (no external side effects) ───────────────────────
    from agents.engineer_agent import finalize_publish_artifacts, run as run_engineer
    from agents.marketing_agent import run as run_marketing
    from agents.qa_agent import run as run_qa

    draft_attempt_limit = 6
    engineer_feedback = ""
    marketing_feedback = ""

    engineer_result_payload: dict = {}
    marketing_result_payload: dict = {}
    qa_payload: dict = {"verdict": "fail", "issues": ["QA has not run yet."]}

    qa_passed = False
    draft_attempts_used = 0

    for attempt in range(1, draft_attempt_limit + 1):
        draft_attempts_used = attempt
        print(f"\n[CEO] Draft improvement cycle {attempt}/{draft_attempt_limit}…")

        bus.send(
            from_agent="ceo",
            to_agent="engineer",
            message_type="task",
            payload={
                "spec": product_spec,
                "focus": tasks["engineer_task"],
                "feedback": engineer_feedback,
                "mode": "draft",
            },
        )
        run_engineer()
        engineer_messages = bus.receive("ceo")
        engineer_result = next(
            (m for m in engineer_messages if m["from_agent"] == "engineer" and m["message_type"] == "result"),
            None,
        )
        engineer_result_payload = engineer_result["payload"] if engineer_result else {"html": "", "status": "missing_result"}

        bus.send(
            from_agent="ceo",
            to_agent="marketing",
            message_type="task",
            payload={
                "spec": product_spec,
                "focus": tasks["marketing_task"],
                "feedback": marketing_feedback,
                "mode": "draft",
                "pr_url": "#",
            },
        )
        run_marketing()
        marketing_messages = bus.receive("ceo")
        marketing_result = next(
            (m for m in marketing_messages if m["from_agent"] == "marketing" and m["message_type"] == "result"),
            None,
        )
        marketing_result_payload = marketing_result["payload"] if marketing_result else {"tagline": "", "status": "missing_result"}

        bus.send(
            from_agent="ceo",
            to_agent="qa",
            message_type="task",
            payload={
                "html": engineer_result_payload.get("html", ""),
                "pr_url": "",
                "marketing_copy": marketing_result_payload,
                "spec": product_spec,
                "post_pr_comments": False,
            },
        )
        run_qa()
        qa_messages = bus.receive("ceo")
        qa_result = next(
            (m for m in qa_messages if m["from_agent"] == "qa" and m["message_type"] == "result"),
            None,
        )
        qa_payload = qa_result["payload"] if qa_result else {"verdict": "fail", "issues": ["QA did not return a result."]}

        qa_passed = qa_payload.get("verdict", "fail") == "pass"

        _log(
            f"Draft cycle {attempt} quality gate",
            f"QA verdict={qa_payload.get('verdict', 'fail')} | Issues={qa_payload.get('issues', [])}",
            "Proceeding to publish phase" if qa_passed else "Preparing next draft iteration",
        )

        if qa_passed:
            break

        engineer_feedback = "QA issues: " + "; ".join(str(i) for i in qa_payload.get("issues", []))
        marketing_feedback_parts = []
        email_feedback = qa_payload.get("email_feedback", "")
        if email_feedback:
            marketing_feedback_parts.append("QA email feedback: " + email_feedback)
        if qa_payload.get("issues"):
            marketing_feedback_parts.append("QA issues: " + "; ".join(str(i) for i in qa_payload.get("issues", [])))
        marketing_feedback = " ".join(marketing_feedback_parts).strip()

    publish_executed = False
    pr_url = ""
    issue_url = ""
    workflow_status = "qa_failed_pre_publish"
    artifact_update_result = {
        "status": "skipped",
        "committed": False,
        "committed_files": [],
        "warnings": [],
        "error": "",
    }

    # ── 5. Publish once after QA pass ───────────────────────────────────
    if qa_passed:
        _log(
            "QA publish gate passed",
            "Draft artifacts are acceptable. Executing one-time publish actions.",
            "Publishing Engineer (GitHub) and Marketing (Email/Slack) once",
        )
        publish_executed = True

        bus.send(
            from_agent="ceo",
            to_agent="engineer",
            message_type="task",
            payload={
                "spec": product_spec,
                "focus": tasks["engineer_task"],
                "mode": "publish",
                "html": engineer_result_payload.get("html", ""),
            },
        )
        run_engineer()
        engineer_messages = bus.receive("ceo")
        engineer_publish_result = next(
            (m for m in engineer_messages if m["from_agent"] == "engineer" and m["message_type"] == "result"),
            None,
        )
        if engineer_publish_result:
            engineer_result_payload = engineer_publish_result["payload"]

        pr_url = engineer_result_payload.get("pr_url", "")
        issue_url = engineer_result_payload.get("issue_url", "")

        bus.send(
            from_agent="ceo",
            to_agent="marketing",
            message_type="task",
            payload={
                "spec": product_spec,
                "focus": tasks["marketing_task"],
                "mode": "publish",
                "pr_url": pr_url,
                "draft_copy": marketing_result_payload,
            },
        )
        run_marketing()
        marketing_messages = bus.receive("ceo")
        marketing_publish_result = next(
            (m for m in marketing_messages if m["from_agent"] == "marketing" and m["message_type"] == "result"),
            None,
        )
        if marketing_publish_result:
            marketing_result_payload = marketing_publish_result["payload"]

        workflow_status = "published"
    else:
        _log(
            "QA publish gate failed",
            f"Draft loop ended after {draft_attempts_used} attempts with verdict={qa_payload.get('verdict', 'fail')}",
            "Skipped all external publish actions",
        )

    # ── 6. Save decision log ───────────────────────────────────────────
    os.makedirs("logs", exist_ok=True)
    with open("logs/ceo_decisions.json", "w", encoding="utf-8") as f:
        json.dump(_decision_log, f, indent=2)
    bus.dump_log()

    if publish_executed:
        artifact_update_result = finalize_publish_artifacts(
            branch=engineer_result_payload.get("branch", ""),
            pr_url=pr_url,
            slack_workspace_url=os.environ.get("SLACK_WORKSPACE_INVITE_URL", "").strip(),
        )
        engineer_result_payload["artifact_update"] = artifact_update_result
        # Backward compatibility for existing consumers that read this key.
        engineer_result_payload["readme_update"] = artifact_update_result

        if artifact_update_result.get("error"):
            print(f"[CEO] Artifact finalization error: {artifact_update_result['error']}")
        for warning in artifact_update_result.get("warnings", []):
            print(f"[CEO] Artifact finalization warning: {warning}")

    print("\n" + "=" * 60)
    print("CEO AGENT COMPLETE")
    print("=" * 60)

    return {
        "idea": idea,
        "product_spec": product_spec,
        "engineer_result": engineer_result_payload,
        "marketing_result": marketing_result_payload,
        "qa_result": qa_payload,
        "pr_url": pr_url,
        "issue_url": issue_url,
        "publish_executed": publish_executed,
        "draft_attempts_used": draft_attempts_used,
        "workflow_status": workflow_status,
        "artifact_update": artifact_update_result,
        "readme_update": artifact_update_result,
        "decision_log": _decision_log,
    }
