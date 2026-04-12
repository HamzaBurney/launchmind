"""
main.py
Entry point for the LaunchMind Multi-Agent System.

Usage:
    python main.py
    python main.py --idea "Your custom startup idea here"

Environment variables required (see .env.example):
    LLM_PROVIDER (openai|groq|gemini)
    OPENAI_API_KEY or GROQ_API_KEY or GEMINI_API_KEY (depends on LLM_PROVIDER)
    GITHUB_TOKEN
    GITHUB_REPO
    SLACK_BOT_TOKEN
    GMAIL_CREDENTIALS_FILE
    GMAIL_TOKEN_FILE
    EMAIL_FROM
    EMAIL_TO
"""

import argparse
import os
import sys
from pathlib import Path

# ── Load .env if present ──────────────────────────────────────────────────
env_path = Path(".env")
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# ── Validate required API keys ────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
PROVIDER_KEY_MAP = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

if LLM_PROVIDER not in PROVIDER_KEY_MAP:
    allowed = ", ".join(sorted(PROVIDER_KEY_MAP))
    print(f"[ERROR] Invalid LLM_PROVIDER='{LLM_PROVIDER}'. Allowed values: {allowed}")
    sys.exit(1)

REQUIRED_ENV = [PROVIDER_KEY_MAP[LLM_PROVIDER]]
OPTIONAL_ENV = [
    "GITHUB_TOKEN",
    "GITHUB_REPO",
    "SLACK_BOT_TOKEN",
    "SLACK_WORKSPACE_INVITE_URL",
    "GMAIL_CREDENTIALS_FILE",
    "GMAIL_TOKEN_FILE",
    "GMAIL_ALLOW_INTERACTIVE_OAUTH",
    "EMAIL_FROM",
    "EMAIL_TO",
]

missing_required = [k for k in REQUIRED_ENV if not os.environ.get(k)]
if missing_required:
    print(f"[ERROR] Missing required environment variables for provider '{LLM_PROVIDER}': {missing_required}")
    print("Please set them in your .env file or export them before running.")
    sys.exit(1)

missing_optional = [k for k in OPTIONAL_ENV if not os.environ.get(k)]
if missing_optional:
    print(f"[WARN] Optional environment variables not set: {missing_optional}")
    print("       Some real-world integrations (GitHub/Slack/Email) will be skipped.\n")

# ── Default startup idea ──────────────────────────────────────────────────
DEFAULT_IDEA = (
    "A CLI tool called 'InvoiceHound' that helps freelancers automatically track "
    "unpaid invoices, sends polite reminder emails on a schedule, and generates "
    "a monthly income report — all from the terminal with zero manual effort."
)


def main():
    parser = argparse.ArgumentParser(description="LaunchMind Multi-Agent System")
    parser.add_argument(
        "--idea",
        type=str,
        default=DEFAULT_IDEA,
        help="The startup idea to run through the MAS",
    )
    parser.add_argument(
        "--max-revisions",
        type=int,
        default=2,
        help="Maximum revision rounds per agent (default: 2)",
    )
    args = parser.parse_args()

    print("\n" + "█" * 60)
    print("█       LAUNCHMIND MULTI-AGENT SYSTEM                    █")
    print("█       Autonomous Startup from Idea → Launch            █")
    print("█" * 60)
    print(f"\nStartup Idea:\n{args.idea}\n")

    from agents.ceo_agent import run as ceo_run

    try:
        results = ceo_run(idea=args.idea, max_revisions=args.max_revisions)
    except KeyboardInterrupt:
        print("\n[MAIN] Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n[MAIN] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # ── Print final summary ───────────────────────────────────────────
    print("\n" + "─" * 60)
    print("FINAL RESULTS SUMMARY")
    print("─" * 60)
    print(f"✅ Startup idea     : {results['idea'][:80]}")
    print(f"✅ Workflow status  : {results.get('workflow_status', 'N/A')}")
    print(f"✅ Draft attempts   : {results.get('draft_attempts_used', 'N/A')}")
    print(f"✅ Published once   : {results.get('publish_executed', False)}")

    spec = results.get("product_spec", {})
    print(f"✅ Value proposition: {spec.get('value_proposition', 'N/A')[:80]}")

    eng = results.get("engineer_result", {}) or {}
    print(f"✅ GitHub PR        : {eng.get('pr_url', 'N/A')}")
    print(f"✅ GitHub Issue     : {eng.get('issue_url', 'N/A')}")
    print(f"✅ GitHub status    : {eng.get('status', 'N/A')}")
    github_errors = eng.get("github_errors", []) or []
    if github_errors:
        print("⚠️ GitHub errors    :")
        for err in github_errors:
            print(f"   - {err}")
    readme_update = eng.get("readme_update", {}) or {}
    if readme_update:
        print(f"✅ README update    : {readme_update.get('status', 'N/A')}")
        readme_warnings = readme_update.get("warnings", []) or []
        if readme_warnings:
            print("⚠️ README warnings  :")
            for warning in readme_warnings:
                print(f"   - {warning}")
        readme_error = readme_update.get("error", "")
        if readme_error:
            print(f"⚠️ README error     : {readme_error}")

    mkt = results.get("marketing_result", {}) or {}
    print(f"✅ Tagline          : {mkt.get('tagline', 'N/A')}")
    print(f"✅ Email sent       : {mkt.get('email_sent', False)}")
    print(f"✅ Slack posted     : {mkt.get('slack_sent', False)}")
    quality_warnings = mkt.get("quality_warnings", []) or []
    if quality_warnings:
        print("⚠️ Email warnings   :")
        for warning in quality_warnings:
            print(f"   - {warning}")

    qa = results.get("qa_result", {}) or {}
    print(f"✅ QA verdict       : {qa.get('verdict', 'N/A').upper()}")

    print("\n📁 Artifacts saved:")
    print("   logs/ceo_decisions.json  — CEO decision log")
    print("   logs/message_bus.json    — Full agent message history")
    print("   output/index.html        — Generated landing page")
    print("\n" + "─" * 60)
    print("LaunchMind MAS complete.")


if __name__ == "__main__":
    main()
