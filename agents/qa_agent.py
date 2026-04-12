"""
agents/qa_agent.py
QA / Reviewer Agent – reviews Engineer HTML and Marketing copy,
posts inline PR review comments on GitHub, returns structured report to CEO.
"""

import json
import os
import re

import requests

from agents.llm_client import generate_text
from message_bus import bus

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GITHUB_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------
def _llm(system: str, user: str, max_tokens: int = 2000) -> str:
    return generate_text(system=system, user=user, max_tokens=max_tokens)


def _parse_json(raw: str, fallback: dict) -> dict:
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

    return fallback


# ---------------------------------------------------------------------------
# HTML Review
# ---------------------------------------------------------------------------
HTML_REVIEW_SYSTEM = """
You are a QA engineer reviewing an HTML landing page against a product spec.
Return ONLY a JSON object (no markdown) with:
{
  "html_verdict": "pass" or "fail",
  "html_issues": ["issue 1", "issue 2", ...],
  "html_score": 1-10,
  "html_comments": [
    {"line_comment": "comment text", "suggestion": "how to fix"}
  ]
}
Check: headline matches value proposition, all features are mentioned, CTA is present,
page is responsive, copy is professional.
"""

COPY_REVIEW_SYSTEM = """
You are a marketing director reviewing marketing copy.
Return ONLY a JSON object (no markdown) with:
{
  "copy_verdict": "pass" or "fail",
  "copy_issues": ["issue 1", "issue 2", ...],
  "copy_score": 1-10,
  "tagline_feedback": "...",
  "email_feedback": "..."
}
Check: tagline is under 10 words and memorable, email has clear CTA, tone is appropriate.
"""


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9]+", text.lower())
        if len(token) >= 4
    }


def _token_prefix(token: str) -> str:
    """Short prefix signature for lightweight fuzzy matching (search/searching, automate/automates)."""
    return token[:5] if len(token) >= 5 else token


def _concept_alignment(headline: str, value_prop: str) -> int:
    concept_groups = [
        {"automate", "automation", "automated", "automates"},
        {"search", "searching", "finder", "discover"},
        {"apply", "application", "applications", "submission", "submissions"},
        {"match", "matching", "matched", "recommend", "recommendation"},
        {"save", "saving", "faster", "time", "efficiency"},
        {"job", "jobs", "career", "roles"},
    ]
    hits = 0
    for group in concept_groups:
        in_headline = any(term in headline for term in group)
        in_value = any(term in value_prop for term in group)
        if in_headline and in_value:
            hits += 1
    return hits


def _headline_matches_value_prop(html: str, spec: dict) -> bool:
    value_prop = str(spec.get("value_proposition", "")).strip()
    if not value_prop:
        return True

    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return False

    headline = _strip_tags(match.group(1)).strip()
    headline_lower = headline.lower()
    value_lower = value_prop.lower()

    if headline_lower in value_lower or value_lower in headline_lower:
        return True

    headline_tokens = _tokenize(headline_lower)
    value_tokens = _tokenize(value_lower)
    if not headline_tokens or not value_tokens:
        return False

    overlap = headline_tokens.intersection(value_tokens)
    overlap_ratio = len(overlap) / max(1, min(len(headline_tokens), len(value_tokens)))
    if len(overlap) >= 2 and overlap_ratio >= 0.15:
        return True

    headline_prefixes = {_token_prefix(token) for token in headline_tokens}
    value_prefixes = {_token_prefix(token) for token in value_tokens}
    fuzzy_overlap = headline_prefixes.intersection(value_prefixes)
    fuzzy_ratio = len(fuzzy_overlap) / max(1, min(len(headline_prefixes), len(value_prefixes)))
    if len(fuzzy_overlap) >= 2 and fuzzy_ratio >= 0.15:
        return True

    # Final semantic safety-net to avoid false failures on stylistic rewrites.
    return _concept_alignment(headline_lower, value_lower) >= 2


def _has_html_cta(html: str) -> bool:
    html_lower = html.lower()
    cta_keywords = (
        "get early access",
        "start free trial",
        "book now",
        "join",
        "sign up",
        "request demo",
        "try now",
        "get started",
    )
    if any(keyword in html_lower for keyword in cta_keywords):
        return True

    # Backup heuristic: any button text or CTA-like anchor text.
    if re.search(r"<button[^>]*>.*?</button>", html, flags=re.IGNORECASE | re.DOTALL):
        return True
    if re.search(r"<a[^>]*>.*?(start|join|book|trial|access|demo).*?</a>", html_lower, flags=re.IGNORECASE | re.DOTALL):
        return True
    return False


def _has_features_section(html: str, spec: dict) -> bool:
    html_lower = html.lower()
    if "feature" in html_lower and re.search(r"<section[^>]*>.*feature", html_lower, flags=re.IGNORECASE | re.DOTALL):
        return True

    features = spec.get("features", []) or []
    feature_names = [str(f.get("name", "")).strip().lower() for f in features if isinstance(f, dict)]
    feature_names = [name for name in feature_names if name]
    if not feature_names:
        return "feature" in html_lower

    required_matches = min(2, len(feature_names))
    present_count = sum(1 for name in feature_names if name in html_lower)
    return present_count >= required_matches


def _has_email_cta(marketing_copy: dict) -> bool:
    email = marketing_copy.get("email", {}) if isinstance(marketing_copy, dict) else {}
    body = str(email.get("body", ""))
    sections = email.get("body_sections", {}) if isinstance(email, dict) else {}
    cta_primary = str(sections.get("cta_primary", "")).strip() if isinstance(sections, dict) else ""
    cta_secondary = str(sections.get("cta_secondary", "")).strip() if isinstance(sections, dict) else ""

    if cta_primary or cta_secondary:
        return True

    body_lower = body.lower()
    cta_keywords = ("book", "start", "join", "claim", "schedule", "try", "reply", "access")
    return any(keyword in body_lower for keyword in cta_keywords)


def _run_critical_checks(html: str, spec: dict, marketing_copy: dict) -> list[str]:
    critical_issues: list[str] = []

    if not _has_html_cta(html):
        critical_issues.append("Critical: Missing CTA in HTML landing page.")

    if not _headline_matches_value_prop(html, spec):
        critical_issues.append("Critical: Headline does not match product value proposition.")

    if not _has_features_section(html, spec):
        critical_issues.append("Critical: Missing features section in HTML.")

    if not _has_email_cta(marketing_copy):
        critical_issues.append("Critical: Email CTA is empty or unclear.")

    return critical_issues


def review_html(html: str, spec: dict) -> dict:
    user = (
        f"Product spec:\n{json.dumps(spec, indent=2)}\n\n"
        f"HTML to review (full content):\n{html}"
    )
    raw = _llm(HTML_REVIEW_SYSTEM, user, max_tokens=1800)
    result = _parse_json(
        raw,
        {
            "html_verdict": "pass",
            "html_issues": ["Could not parse QA JSON output; accepted by fallback."],
            "html_score": 6,
            "html_comments": [],
        },
    )
    print(f"[QA] HTML review: verdict={result.get('html_verdict')} score={result.get('html_score')}")
    return result


def review_copy(copy: dict, spec: dict) -> dict:
    user = (
        f"Product spec:\n{json.dumps(spec, indent=2)}\n\n"
        f"Marketing copy:\n{json.dumps(copy, indent=2)}"
    )
    raw = _llm(COPY_REVIEW_SYSTEM, user, max_tokens=1000)
    result = _parse_json(
        raw,
        {
            "copy_verdict": "pass",
            "copy_issues": ["Could not parse QA JSON output; accepted by fallback."],
            "copy_score": 6,
            "tagline_feedback": "",
            "email_feedback": "",
        },
    )
    print(f"[QA] Copy review: verdict={result.get('copy_verdict')} score={result.get('copy_score')}")
    return result


# ---------------------------------------------------------------------------
# GitHub PR review comments
# ---------------------------------------------------------------------------
def _get_pr_number(pr_url: str) -> str | None:
    """Extract PR number from URL like https://github.com/owner/repo/pull/42"""
    match = re.search(r'/pull/(\d+)', pr_url)
    return match.group(1) if match else None


def _get_pr_commit_id(pr_number: str) -> tuple[str, str] | None:
    """Return (head_sha, pull_request_review_id) for creating review comments."""
    resp = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/pulls/{pr_number}",
        headers=GITHUB_HEADERS,
    )
    if resp.ok:
        data = resp.json()
        return data["head"]["sha"]
    return None


def post_pr_review_comments(pr_url: str, html_review: dict) -> bool:
    """Post inline review comments on the PR using the GitHub Pull Request Review API."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("[QA] GitHub not configured – skipping PR comments.")
        return False

    pr_number = _get_pr_number(pr_url)
    if not pr_number:
        print(f"[QA] Could not extract PR number from: {pr_url}")
        return False

    head_sha = _get_pr_commit_id(pr_number)
    if not head_sha:
        print("[QA] Could not get PR commit SHA.")
        return False

    comments_data = html_review.get("html_comments", [])
    if not comments_data:
        # Fallback comments
        comments_data = [
            {"line_comment": "QA: Ensure the value proposition headline is prominently displayed above the fold.", "suggestion": "Move the H1 closer to the top of the page."},
            {"line_comment": "QA: Verify all features listed in the product spec appear in the features section.", "suggestion": "Cross-reference with the spec and add any missing features."},
        ]

    # Build PR review with comments using the reviews API
    review_comments = []
    for i, c in enumerate(comments_data[:4]):  # Max 4 comments
        review_comments.append({
            "path": "output/index.html",
            "position": (i + 1) * 5,  # approximate diff position
            "body": f"**QA Comment:** {c.get('line_comment', '')}\n\n💡 **Suggestion:** {c.get('suggestion', '')}",
        })

    issues = html_review.get("html_issues", [])
    verdict = html_review.get("html_verdict", "pass")
    review_body = (
        f"## 🤖 QA Agent Review\n\n"
        f"**Verdict:** {'✅ PASS' if verdict == 'pass' else '❌ FAIL'}\n"
        f"**Score:** {html_review.get('html_score', 'N/A')}/10\n\n"
    )
    if issues:
        review_body += "**Issues found:**\n" + "\n".join(f"- {i}" for i in issues)

    event = "APPROVE" if verdict == "pass" else "REQUEST_CHANGES"

    try:
        resp = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/pulls/{pr_number}/reviews",
            headers=GITHUB_HEADERS,
            json={
                "commit_id": head_sha,
                "body": review_body,
                "event": event,
                "comments": review_comments,
            },
            timeout=20,
        )
        if resp.ok:
            print(f"[QA] Posted PR review (event={event}) on PR #{pr_number}")
            return True
        else:
            print(f"[QA] PR review failed {resp.status_code}: {resp.text[:200]}")
            # Try a simpler comment as fallback
            comment_resp = requests.post(
                f"https://api.github.com/repos/{GITHUB_REPO}/issues/{pr_number}/comments",
                headers=GITHUB_HEADERS,
                json={"body": review_body},
                timeout=15,
            )
            if comment_resp.ok:
                print("[QA] Posted fallback PR comment.")
                return True
            return False
    except Exception as e:
        print(f"[QA] PR review error: {e}")
        return False


# ---------------------------------------------------------------------------
# Agent run loop
# ---------------------------------------------------------------------------
def run():
    print("\n[QA] Agent starting…")
    messages = bus.receive("qa")

    if not messages:
        print("[QA] No messages – nothing to do.")
        return

    msg = messages[-1]
    payload = msg["payload"]
    html = payload.get("html", "")
    pr_url = payload.get("pr_url", "")
    marketing_copy = payload.get("marketing_copy", {})
    spec = payload.get("spec", {})
    post_pr_comments = bool(payload.get("post_pr_comments", True))

    print(f"[QA] Reviewing HTML ({len(html)} chars) and marketing copy…")

    # 1. Review HTML
    html_review = review_html(html, spec) if html else {"html_verdict": "fail", "html_issues": ["No HTML provided"], "html_score": 0, "html_comments": []}

    # 2. Review marketing copy
    copy_review = review_copy(marketing_copy, spec) if marketing_copy else {"copy_verdict": "fail", "copy_issues": ["No copy provided"], "copy_score": 0}

    # 3. Post PR review comments
    if post_pr_comments and pr_url and pr_url != "#":
        post_pr_review_comments(pr_url, html_review)

    # 4. Overall verdict
    critical_issues = _run_critical_checks(html=html, spec=spec, marketing_copy=marketing_copy)

    html_score_threshold = _as_int(payload.get("html_score_threshold", 7), default=7)
    copy_score_threshold = _as_int(payload.get("copy_score_threshold", 7), default=7)
    html_score = _as_int(html_review.get("html_score"), default=0)
    copy_score = _as_int(copy_review.get("copy_score"), default=0)
    scores_pass = html_score >= html_score_threshold and copy_score >= copy_score_threshold

    overall_verdict = "pass" if (not critical_issues and scores_pass) else "fail"

    all_issues = html_review.get("html_issues", []) + copy_review.get("copy_issues", []) + critical_issues

    report = {
        "verdict": overall_verdict,
        "html_verdict": html_review.get("html_verdict"),
        "html_score": html_score,
        "copy_verdict": copy_review.get("copy_verdict"),
        "copy_score": copy_score,
        "issues": all_issues,
        "critical_issues": critical_issues,
        "score_thresholds": {
            "html": html_score_threshold,
            "copy": copy_score_threshold,
        },
        "score_gate_passed": scores_pass,
        "html_feedback": copy_review.get("tagline_feedback", ""),
        "email_feedback": copy_review.get("email_feedback", ""),
    }

    print(
        f"[QA] Overall verdict: {overall_verdict.upper()} | "
        f"Critical: {len(critical_issues)} | Issues: {len(all_issues)} | "
        f"Score gate: {'PASS' if scores_pass else 'FAIL'}"
    )

    # 5. Send report to CEO
    bus.send(
        from_agent="qa",
        to_agent="ceo",
        message_type="result",
        payload=report,
        parent_message_id=msg["message_id"],
    )
    print("[QA] Review report sent to CEO.")
