# 🚀 LaunchMind – Multi-Agent Startup System

LaunchMind is a fully autonomous Multi-Agent System (MAS) that takes a startup idea and runs it end-to-end: generating a product spec, iterating through draft improvements, QA-gating the output, and then publishing exactly once (GitHub issue/PR, email, Slack) only after QA pass.

**Startup Idea (default):** *InvoiceHound* — A CLI tool that helps freelancers automatically track unpaid invoices, sends polite reminder emails on a schedule, and generates a monthly income report from the terminal.

---

## 🏗 Agent Architecture

```
                        ┌─────────────────────┐
    Startup Idea ──────▶│     CEO AGENT       │◀──────── QA Report
                        │   (Orchestrator)    │
                        │  - Decomposes idea  │
                        │  - Reviews outputs  │──────────────────────┐
                        │  - Dynamic routing  │                      │
                        └──────┬──────────────┘                      │
                               │                                      │
               ┌───────────────┼───────────────┐                     │
               │               │               │                     │
               ▼               ▼               ▼                     │
    ┌─────────────────┐        │    ┌─────────────────┐              │
    │  PRODUCT AGENT  │        │    │ MARKETING AGENT │              │
    │  - Value prop   │        │    │  - Tagline      │              │
    │  - 3 Personas   │        │    │  - Email copy   │              │
    │  - 5 Features   │        │    │  - Social posts │              │
    │  - User stories │        │    │  - Gmail API    │              │
    └────────┬────────┘        │    │  - Slack Block  │              │
             │                 │    └────────┬────────┘              │
             │ spec            │             │ copy                  │
             ▼                 │             ▼                       │
    ┌─────────────────┐        │    ┌─────────────────┐              │
    │ ENGINEER AGENT  │        │    │    QA AGENT     │◀─────────────┘
    │  - Gen HTML     │        │    │  - Review HTML  │
    │  - GitHub issue │        │    │  - Review copy  │
    │  - Git commit   │        │    │  - PR comments  │
    │  - Open PR      │        │    │  - Pass/Fail    │
    └─────────────────┘        │    └─────────────────┘
                               │
                    Message Bus (JSON)
```

### Agent Responsibilities

| Agent | Role | Real-World Actions |
|---|---|---|
| **CEO** | Orchestrator | LLM task decomposition, draft-cycle orchestration, QA gate, one-time publish trigger |
| **Product** | PM | Generates value prop, personas, features, user stories |
| **Engineer** | Builder | Drafts HTML during revision cycles, then publishes once to GitHub after QA pass |
| **Marketing** | Growth | Drafts copy during revision cycles, then sends one email and one Slack post after QA pass |
| **QA** | Reviewer | Reviews HTML + copy each draft cycle, performs deterministic critical checks and score gates |

---

## 📋 Message Schema

All inter-agent messages follow this schema:

```json
{
  "message_id": "msg-a1b2c3d4",
  "from_agent": "ceo",
  "to_agent": "product",
  "message_type": "task",
  "payload": { "idea": "...", "focus": "..." },
  "timestamp": "2025-10-01T09:00:00Z",
  "parent_message_id": "msg-00000000"
}
```

`message_type` is one of: `task` | `result` | `revision_request` | `confirmation`

All messages are persisted to `logs/message_bus.json` at runtime.

---

## 🔄 Execution Model

LaunchMind now uses a **draft → QA gate → publish once** model:

1. **Product loop**: CEO reviews Product output and can request revisions.
2. **Draft loop (up to 4 cycles)**: Engineer and Marketing run in draft mode with **no external side effects**.
3. **QA gate**: QA evaluates draft HTML + copy using critical checks and score thresholds.
4. **Publish once**: Only when QA passes, CEO triggers one publish call for Engineer (Issue + PR) and one publish call for Marketing (Email + Slack).
5. **Fail-safe**: If QA does not pass within the draft limit, publish is skipped entirely.

---

## ⚙️ Setup

### 1. Clone & install

```bash
git clone https://github.com/your-username/launchmind-your-group.git
cd launchmind-your-group
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in all values
```

Required:
- `LLM_PROVIDER` — `openai`, `groq`, or `gemini` (defaults to `openai`)
- If `LLM_PROVIDER=openai`: `OPENAI_API_KEY` — from [platform.openai.com](https://platform.openai.com)
- If `LLM_PROVIDER=groq`: `GROQ_API_KEY` — from [console.groq.com](https://console.groq.com)
- If `LLM_PROVIDER=gemini`: `GEMINI_API_KEY` — from [aistudio.google.com](https://aistudio.google.com)

Optional model overrides:
- `OPENAI_MODEL` — defaults to `gpt-4o-mini`
- `GROQ_MODEL` — defaults to `llama-3.3-70b-versatile`
- `GEMINI_MODEL` — defaults to `gemini-2.0-flash`

Optional (enables real integrations):
- `GITHUB_TOKEN` + `GITHUB_REPO` — GitHub PAT with `repo` and `workflow` scopes
- `SLACK_BOT_TOKEN` — Slack Bot token (`xoxb-...`)
- `SLACK_WORKSPACE_INVITE_URL` — public Slack workspace invite link used to auto-update README links after publish
- `GMAIL_CREDENTIALS_FILE` + `GMAIL_TOKEN_FILE` + `EMAIL_FROM` + `EMAIL_TO` — Gmail API credentials

### 3. Platform setup

**GitHub:**
```bash
# Test your token
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user

# Test repository access
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/repos/$GITHUB_REPO
```

If GitHub issue/PR creation is not happening, check:
- `GITHUB_REPO` is exactly `owner/repo` with no spaces
- token has `repo` and `workflow` scopes
- token owner has write access to the target repository
- token is not expired or revoked

**Slack:**
1. Create app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add scopes: `chat:write`, `channels:read`, `channels:join`
3. Install to workspace, copy `xoxb-` token
4. Create `#launches` channel, invite the bot
5. Create a workspace invite link and set `SLACK_WORKSPACE_INVITE_URL` in `.env`

**Gmail API (OAuth Desktop):**
1. Open [Google Cloud Console](https://console.cloud.google.com)
2. Create or select a project
3. Enable **Gmail API** in **APIs & Services → Library**
4. Configure the **OAuth consent screen** (External is fine for testing)
5. Create credentials in **APIs & Services → Credentials → Create Credentials → OAuth client ID**
6. Choose **Desktop app** and download the JSON file
7. Save it locally, for example as `gmail_credentials.json`
8. In `.env`, set:
  - `GMAIL_CREDENTIALS_FILE=gmail_credentials.json`
  - `GMAIL_TOKEN_FILE=gmail_token.json`
  - `GMAIL_ALLOW_INTERACTIVE_OAUTH=true` (first run only)
  - `EMAIL_FROM=your_gmail_address@gmail.com`
  - `EMAIL_TO=recipient@example.com`
9. Run `python main.py` once and complete the browser OAuth prompt. A token file will be created.
10. Set `GMAIL_ALLOW_INTERACTIVE_OAUTH=false` for normal non-interactive runs.

### 4. Run the system

```bash
# Default idea (InvoiceHound)
python main.py

# Custom startup idea
python main.py --idea "A mobile app that helps dog owners find nearby vets with real-time availability"

# Product-loop revisions only (draft loop remains fixed at 4 cycles)
python main.py --idea "Your idea" --max-revisions 0
```

---

## 📁 Repository Structure

```
launchmind/
├── main.py                  # Entry point – runs the full MAS
├── message_bus.py           # Shared JSON message bus (singleton)
├── requirements.txt
├── .env.example             # Environment variable template
├── .gitignore               # Excludes .env from commits
├── README.md
└── agents/
    ├── llm_client.py        # Shared OpenAI-compatible client (OpenAI + Groq + Gemini)
    ├── ceo_agent.py         # Orchestrator with LLM review + dynamic routing
    ├── product_agent.py     # Product spec generation
    ├── engineer_agent.py    # Draft HTML + one-time GitHub publish
    ├── marketing_agent.py   # Draft copy + one-time Gmail/Slack publish
    ├── qa_agent.py          # Deterministic QA gate with critical checks
    └── gmail_client.py      # Gmail API OAuth helper
```

---

## 🌐 Platform Integrations

| Platform | What the agent does |
|---|---|
| **OpenAI / Groq / Gemini APIs** | All 5 agents use a shared OpenAI-compatible LLM client. Provider is selected with `LLM_PROVIDER`. |
| **GitHub** | Engineer publishes once after QA pass: creates one issue, commits `index.html`, opens one PR, then commits updated `README.md` plus files under `output/` and `logs/` on the same branch. |
| **Slack** | Marketing publishes one Block Kit launch announcement to `#launches` after QA pass. |
| **Gmail API** | Marketing sends one outreach email after QA pass. |

---

## 📤 Output Artifacts

After running, you'll find:

- `output/index.html` — The generated landing page
- `logs/ceo_decisions.json` — Every decision the CEO made and why
- `logs/message_bus.json` — Full audit trail of all inter-agent messages

---

## 🔗 Links

These values are auto-updated after a successful publish run.

<<<<<<< HEAD
- **GitHub PR (Engineer):** [https://github.com/HamzaBurney/launchmind/pull/35](https://github.com/HamzaBurney/launchmind/pull/35)
=======
- **GitHub PR (Engineer):** [https://github.com/HamzaBurney/launchmind/pull/35](https://github.com/HamzaBurney/launchmind/pull/35)
>>>>>>> 7623cf04671e8e8b735e0425d76b9779b9501bf7
- **Slack workspace:** [https://join.slack.com/t/launchmindtalk/shared_invite/zt-3ucudhbnm-szzuxbhFVzK3dHu6lC~rLg](https://join.slack.com/t/launchmindtalk/shared_invite/zt-3ucudhbnm-szzuxbhFVzK3dHu6lC~rLg)