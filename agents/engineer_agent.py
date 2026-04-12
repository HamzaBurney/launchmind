"""
agents/engineer_agent.py
Engineer Agent – generates HTML landing page and interacts with GitHub.

Real actions:
  - Creates a GitHub issue
  - Commits index.html to a new branch
  - Opens a pull request
"""

import base64
import json
import os
import re
import time

import requests

from agents.llm_client import generate_text
from message_bus import bus

# GitHub config from environment
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # format: "owner/repo"
GITHUB_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
AGENT_AUTHOR = {
    "name": "EngineerAgent",
    "email": "agent@launchmind.ai",
}

README_FILE_PATH = "README.md"
README_PR_LINE_PATTERN = r"^- \*\*GitHub PR \(Engineer\):\*\* .*$"
README_SLACK_LINE_PATTERN = r"^- \*\*Slack workspace:\*\* .*$"
ARTIFACT_DIRECTORIES = ("output", "logs")


def _response_error(resp: requests.Response, action: str) -> str:
    try:
        data = resp.json()
        message = data.get("message", "")
        errors = data.get("errors")
        details = f" | errors={errors}" if errors else ""
        return f"{action} failed (HTTP {resp.status_code}): {message}{details}"
    except ValueError:
        return f"{action} failed (HTTP {resp.status_code}): {resp.text[:300]}"


def _validate_github_config() -> str | None:
    if not GITHUB_TOKEN:
        return "Missing GITHUB_TOKEN."
    if not GITHUB_REPO:
        return "Missing GITHUB_REPO."
    if GITHUB_REPO.count("/") != 1:
        return f"Invalid GITHUB_REPO '{GITHUB_REPO}'. Expected format 'owner/repo'."
    if " " in GITHUB_REPO:
        return f"Invalid GITHUB_REPO '{GITHUB_REPO}'. Repository value must not contain spaces."
    return None


def _is_empty_repo_error(error_message: str) -> bool:
    return "Git Repository is empty" in error_message


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------
def _llm(system: str, user: str, max_tokens: int = 4000) -> str:
    return generate_text(system=system, user=user, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------
HTML_SYSTEM = """
You are a senior frontend engineer. Generate a complete, beautiful, single-file HTML landing page
for a startup. The page must include:
- A compelling headline and sub-headline derived from the value proposition
- A features section listing all product features
- A prominent call-to-action button (e.g. "Get Early Access" or "Start Free Trial")
- Professional CSS styling inline in a <style> tag (no external dependencies)
- Responsive design with a mobile-first approach
- A footer with the product name

Critical headline rule:
- The <h1> must stay semantically aligned with the value proposition and reuse at least 3 key terms from it
    (for example concepts like automate, job search, application, matching, time-saving).

Use modern CSS: gradients, shadows, clean typography (Google Fonts via @import is fine).
Return ONLY the raw HTML - no explanation, no markdown fences, just the HTML starting with <!DOCTYPE html>.
"""


def generate_html(spec: dict, feedback: str = "") -> str:
    feedback_section = f"\n\nFeedback to incorporate:\n{feedback}" if feedback else ""
    value_prop = spec.get('value_proposition', '')
    user_prompt = (
        f"Value proposition: {value_prop}\n"
        f"Features:\n{json.dumps(spec.get('features', []), indent=2)}\n"
        f"User personas:\n{json.dumps(spec.get('personas', []), indent=2)}\n"
        f"User stories:\n{json.dumps(spec.get('user_stories', []), indent=2)}"
        f"\nInstruction: Ensure the <h1> directly reflects this value proposition wording and keeps key terms visible."
        f"{feedback_section}"
    )
    html = _llm(HTML_SYSTEM, user_prompt, max_tokens=4000)
    # Strip any accidental markdown fences
    html = re.sub(r'^```html?\s*', '', html, flags=re.IGNORECASE)
    html = re.sub(r'\s*```$', '', html)
    return html.strip()


def generate_pr_details(spec: dict) -> tuple[str, str]:
    """Generate PR title and body using LLM."""
    system = (
        "You are a software engineer writing a GitHub PR. "
        "Return ONLY a JSON object with keys 'title' (string) and 'body' (markdown string). "
        "The body should describe what was built and why."
    )
    user = f"Product spec summary: {spec.get('value_proposition', '')}\nFeatures: {[f['name'] for f in spec.get('features', [])]}"
    raw = _llm(system, user, max_tokens=800)
    try:
        data = json.loads(raw)
        return data["title"], data["body"]
    except Exception:
        return "Initial landing page", f"Auto-generated landing page for: {spec.get('value_proposition', '')}"


def generate_issue_body(spec: dict) -> str:
    system = (
        "You are a software engineer writing a GitHub issue. "
        "Write a concise but thorough issue description for building a landing page. "
        "Include: what needs to be built, acceptance criteria, and which features to highlight. "
        "Return plain markdown text."
    )
    user = f"Spec: {json.dumps(spec, indent=2)}"
    return _llm(system, user, max_tokens=600)


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------
def _gh(method: str, path: str, log_errors: bool = True, **kwargs) -> requests.Response:
    url = f"https://api.github.com/repos/{GITHUB_REPO}{path}"
    if "timeout" not in kwargs:
        kwargs["timeout"] = 20
    resp = requests.request(method, url, headers=GITHUB_HEADERS, **kwargs)
    if log_errors and not resp.ok:
        print(f"[ENGINEER] GitHub API error {resp.status_code}: {resp.text[:300]}")
    return resp


def get_default_branch_sha() -> tuple[str, str, str | None]:
    resp = _gh("GET", "")
    if not resp.ok:
        return "", "", _response_error(resp, "Load repository metadata")

    repo_data = resp.json()
    default_branch = repo_data.get("default_branch", "main")

    ref_resp = _gh("GET", f"/git/ref/heads/{default_branch}")
    if not ref_resp.ok:
        return "", default_branch, _response_error(ref_resp, f"Load default branch ref '{default_branch}'")

    sha = (ref_resp.json().get("object") or {}).get("sha")
    if not sha:
        return "", default_branch, "Default branch ref response did not include a SHA."

    return sha, default_branch, None


def bootstrap_empty_repository(default_branch: str) -> str | None:
    """Create an initial commit on an empty repository so branch-based flow can proceed."""
    init_content = "# LaunchMind\n\nRepository initialized by EngineerAgent.\n"

    blob_resp = _gh("POST", "/git/blobs", json={"content": init_content, "encoding": "utf-8"})
    if not blob_resp.ok:
        return _response_error(blob_resp, "Create initial blob")
    blob_sha = blob_resp.json().get("sha")
    if not blob_sha:
        return "Create initial blob failed: response did not include blob SHA."

    tree_resp = _gh(
        "POST",
        "/git/trees",
        json={
            "tree": [
                {
                    "path": "README.md",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            ]
        },
    )
    if not tree_resp.ok:
        return _response_error(tree_resp, "Create initial tree")
    tree_sha = tree_resp.json().get("sha")
    if not tree_sha:
        return "Create initial tree failed: response did not include tree SHA."

    commit_resp = _gh(
        "POST",
        "/git/commits",
        json={
            "message": "chore: initialize repository",
            "tree": tree_sha,
            "parents": [],
            "author": AGENT_AUTHOR,
            "committer": AGENT_AUTHOR,
        },
    )
    if not commit_resp.ok:
        return _response_error(commit_resp, "Create initial commit")
    commit_sha = commit_resp.json().get("sha")
    if not commit_sha:
        return "Create initial commit failed: response did not include commit SHA."

    ref_resp = _gh(
        "POST",
        "/git/refs",
        json={"ref": f"refs/heads/{default_branch}", "sha": commit_sha},
    )
    if not ref_resp.ok and ref_resp.status_code != 422:
        return _response_error(ref_resp, f"Create default branch ref '{default_branch}'")

    print(f"[ENGINEER] Initialized empty repository on branch '{default_branch}'.")
    return None


def create_branch(branch_name: str, sha: str) -> tuple[bool, str | None]:
    resp = _gh("POST", "/git/refs", json={
        "ref": f"refs/heads/{branch_name}",
        "sha": sha,
    })
    if resp.status_code == 422:
        print(f"[ENGINEER] Branch '{branch_name}' already exists – will use it.")
        return True, None
    if not resp.ok:
        return False, _response_error(resp, f"Create branch '{branch_name}'")
    return True, None


def commit_file_bytes(branch: str, filename: str, content: bytes, commit_message: str, base_sha: str) -> tuple[bool, str | None]:
    encoded = base64.b64encode(content).decode("ascii")

    # Check if file already exists on that branch (to get its sha for update)
    existing = _gh("GET", f"/contents/{filename}", log_errors=False, params={"ref": branch})
    payload = {
        "message": commit_message,
        "content": encoded,
        "branch": branch,
        "committer": AGENT_AUTHOR,
        "author": AGENT_AUTHOR,
    }
    if existing.ok:
        payload["sha"] = existing.json()["sha"]

    resp = _gh("PUT", f"/contents/{filename}", json=payload)
    if not resp.ok:
        return False, _response_error(resp, f"Commit '{filename}' to branch '{branch}'")
    return True, None


def commit_file(branch: str, filename: str, content: str, commit_message: str, base_sha: str) -> tuple[bool, str | None]:
    return commit_file_bytes(
        branch=branch,
        filename=filename,
        content=content.encode("utf-8"),
        commit_message=commit_message,
        base_sha=base_sha,
    )


def create_github_issue(title: str, body: str) -> tuple[str, str | None]:
    resp = _gh("POST", "/issues", json={"title": title, "body": body, "labels": ["agent-generated"]})
    if resp.ok:
        url = resp.json().get("html_url", "")
        if not url:
            return "", "Issue was created but no html_url was returned by GitHub."
        print(f"[ENGINEER] Issue created: {url}")
        return url, None
    return "", _response_error(resp, "Create GitHub issue")


def open_pull_request(title: str, body: str, head_branch: str, base_branch: str) -> tuple[str, str | None]:
    resp = _gh("POST", "/pulls", json={
        "title": title,
        "body": body,
        "head": head_branch,
        "base": base_branch,
    })
    if resp.ok:
        url = resp.json().get("html_url", "")
        if not url:
            return "", "PR was created but no html_url was returned by GitHub."
        print(f"[ENGINEER] PR opened: {url}")
        return url, None
    # If PR already exists
    if resp.status_code == 422:
        pulls = _gh("GET", "/pulls", params={"head": f"{GITHUB_REPO.split('/')[0]}:{head_branch}", "state": "open"})
        if pulls.ok and pulls.json():
            return pulls.json()[0].get("html_url", ""), None
    return "", _response_error(resp, f"Open pull request from '{head_branch}' to '{base_branch}'")


# ---------------------------------------------------------------------------
# README finalization helpers
# ---------------------------------------------------------------------------
def _render_readme_links(readme_text: str, pr_url: str, slack_workspace_url: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    updated = readme_text

    pr_line = f"- **GitHub PR (Engineer):** [{pr_url}]({pr_url})"
    updated, pr_replacements = re.subn(
        README_PR_LINE_PATTERN,
        pr_line,
        updated,
        flags=re.MULTILINE,
    )
    if pr_replacements == 0:
        warnings.append("README links section is missing the GitHub PR bullet; skipped PR link update.")

    if slack_workspace_url:
        slack_line = f"- **Slack workspace:** [{slack_workspace_url}]({slack_workspace_url})"
        updated, slack_replacements = re.subn(
            README_SLACK_LINE_PATTERN,
            slack_line,
            updated,
            flags=re.MULTILINE,
        )
        if slack_replacements == 0:
            warnings.append("README links section is missing the Slack workspace bullet; skipped Slack link update.")
    else:
        warnings.append("SLACK_WORKSPACE_INVITE_URL is not set; README Slack workspace link was left unchanged.")

    return updated, warnings


def _collect_artifact_files() -> list[str]:
    files: list[str] = []
    for directory in ARTIFACT_DIRECTORIES:
        if not os.path.isdir(directory):
            continue

        for root, _, filenames in os.walk(directory):
            filenames.sort()
            for filename in filenames:
                full_path = os.path.join(root, filename)
                if not os.path.isfile(full_path):
                    continue
                relative = os.path.relpath(full_path, ".").replace("\\", "/")
                files.append(relative)

    files.sort()
    return files


def finalize_publish_artifacts(branch: str, pr_url: str, slack_workspace_url: str) -> dict:
    """Update README links and commit README + artifact files to an existing publish branch."""
    result = {
        "status": "skipped",
        "committed": False,
        "committed_files": [],
        "warnings": [],
        "error": "",
    }

    config_error = _validate_github_config()
    if config_error:
        result["status"] = "failed"
        result["error"] = f"README finalization aborted: {config_error}"
        return result

    if not branch:
        result["warnings"].append("Artifact finalization skipped: missing publish branch name.")
        return result

    if not pr_url:
        result["warnings"].append("Artifact finalization skipped: missing PR URL.")
        return result

    if not os.path.exists(README_FILE_PATH):
        result["status"] = "failed"
        result["error"] = "Artifact finalization failed: README.md does not exist locally."
        return result

    try:
        with open(README_FILE_PATH, "r", encoding="utf-8") as f:
            original = f.read()
    except OSError as exc:
        result["status"] = "failed"
        result["error"] = f"Artifact finalization failed while reading README.md: {exc}"
        return result

    updated, warnings = _render_readme_links(
        readme_text=original,
        pr_url=pr_url,
        slack_workspace_url=slack_workspace_url,
    )
    result["warnings"].extend(warnings)

    if updated != original:
        try:
            with open(README_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(updated)
        except OSError as exc:
            result["status"] = "failed"
            result["error"] = f"Artifact finalization failed while writing README.md: {exc}"
            return result

        commit_ok, commit_error = commit_file(
            branch=branch,
            filename=README_FILE_PATH,
            content=updated,
            commit_message="docs: update launch links in README\n\nGenerated by EngineerAgent",
            base_sha="",
        )
        if not commit_ok:
            result["status"] = "failed"
            result["error"] = commit_error or "Artifact finalization failed while committing README.md."
            return result
        result["committed_files"].append(README_FILE_PATH)

    artifact_files = _collect_artifact_files()
    if not artifact_files:
        result["warnings"].append("No artifact files found under output/ or logs/.")

    for artifact_path in artifact_files:
        if artifact_path == "output/index.html":
            # Already committed during the publish step.
            continue

        try:
            with open(artifact_path, "rb") as f:
                content_bytes = f.read()
        except OSError as exc:
            result["warnings"].append(f"Could not read artifact '{artifact_path}': {exc}")
            continue

        commit_ok, commit_error = commit_file_bytes(
            branch=branch,
            filename=artifact_path,
            content=content_bytes,
            commit_message=f"chore: update artifact {artifact_path}\n\nGenerated by EngineerAgent",
            base_sha="",
        )
        if not commit_ok:
            result["warnings"].append(commit_error or f"Failed to commit artifact '{artifact_path}'.")
            continue

        result["committed_files"].append(artifact_path)

    if result["committed_files"]:
        result["status"] = "updated"
        result["committed"] = True
    else:
        result["status"] = "no_changes"

    return result


# ---------------------------------------------------------------------------
# Agent run loop
# ---------------------------------------------------------------------------
def run():
    print("\n[ENGINEER] Agent starting…")
    messages = bus.receive("engineer")

    if not messages:
        print("[ENGINEER] No messages in inbox – nothing to do.")
        return

    # Use the most recent task/revision message from CEO
    relevant = [m for m in messages if m["from_agent"] in ("ceo", "product") and m["message_type"] in ("task", "revision_request", "result")]
    if not relevant:
        print("[ENGINEER] No relevant messages found.")
        return

    msg = relevant[-1]
    payload = msg["payload"]
    spec = payload.get("spec", {})
    feedback = payload.get("feedback", "") if msg["message_type"] == "revision_request" else ""
    mode = str(payload.get("mode", "publish")).strip().lower()
    should_publish = mode == "publish"

    print(f"[ENGINEER] Processing: type={msg['message_type']} mode={mode}")

    # 1. Generate HTML
    print("[ENGINEER] Preparing HTML landing page…")
    html_content = payload.get("html", "") or generate_html(spec, feedback)
    print(f"[ENGINEER] Generated {len(html_content)} chars of HTML.")

    # 2. Save locally
    os.makedirs("output", exist_ok=True)
    with open("output/index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("[ENGINEER] Saved output/index.html")

    issue_url = ""
    pr_url = ""
    branch_name = ""
    github_errors: list[str] = []
    github_status = "draft_ready"

    if should_publish:
        branch_name = f"agent-landing-page-{int(time.time())}"
        config_error = _validate_github_config()

        if config_error:
            print(f"[ENGINEER] GitHub config error: {config_error} Skipping GitHub actions.")
            github_errors.append(config_error)
            github_status = "github_failed"
        else:
            try:
                github_status = "github_in_progress"

                # 3. Get base SHA and default branch
                base_sha, default_branch, base_error = get_default_branch_sha()
                if base_error:
                    if _is_empty_repo_error(base_error):
                        init_error = bootstrap_empty_repository(default_branch or "main")
                        if init_error:
                            github_errors.append(init_error)
                        else:
                            base_sha, default_branch, base_error = get_default_branch_sha()
                            if base_error:
                                github_errors.append(base_error)
                    else:
                        github_errors.append(base_error)

                # 4. Create branch
                branch_created = False
                if base_sha:
                    branch_created, branch_error = create_branch(branch_name, base_sha)
                    if branch_error:
                        github_errors.append(branch_error)
                else:
                    github_errors.append("Skipping branch creation because default branch SHA could not be resolved.")

                # 5. Commit HTML file
                commit_ok = False
                if branch_created:
                    commit_ok, commit_error = commit_file(
                        branch=branch_name,
                        filename="output/index.html",
                        content=html_content,
                        commit_message="feat: add AI-generated landing page\n\nGenerated by EngineerAgent",
                        base_sha=base_sha,
                    )
                    if commit_error:
                        github_errors.append(commit_error)
                    if commit_ok:
                        print(f"[ENGINEER] Committed output/index.html to branch '{branch_name}'")
                else:
                    github_errors.append("Skipping commit because branch was not created.")

                # 6. Create GitHub issue
                issue_body = generate_issue_body(spec)
                issue_url, issue_error = create_github_issue("Initial landing page", issue_body)
                if issue_error:
                    github_errors.append(issue_error)

                # 7. Open pull request only if branch+commit succeeded
                if branch_created and commit_ok and default_branch:
                    pr_title, pr_body = generate_pr_details(spec)
                    pr_url, pr_error = open_pull_request(pr_title, pr_body, branch_name, default_branch)
                    if pr_error:
                        github_errors.append(pr_error)
                else:
                    github_errors.append("Skipping pull request creation because branch/commit step did not complete.")

                if issue_url and pr_url and not github_errors:
                    github_status = "complete"
                elif issue_url or pr_url:
                    github_status = "partial"
                else:
                    github_status = "github_failed"

            except Exception as e:
                github_errors.append(f"Unexpected GitHub operation failure: {e}")
                github_status = "github_failed"
    else:
        print("[ENGINEER] Draft mode – skipping GitHub publishing.")

    if github_errors:
        print("[ENGINEER] GitHub step warnings/errors:")
        for err in github_errors:
            print(f"  - {err}")

    # 8. Report back to CEO
    bus.send(
        from_agent="engineer",
        to_agent="ceo",
        message_type="result",
        payload={
            "html": html_content,
            "pr_url": pr_url,
            "issue_url": issue_url,
            "branch": branch_name,
            "status": github_status,
            "github_errors": github_errors,
            "mode": mode,
            "published": should_publish and github_status in {"complete", "partial"},
        },
        parent_message_id=msg["message_id"],
    )
    print(f"[ENGINEER] Done. Status: {github_status} | PR: {pr_url} | Issue: {issue_url}")
