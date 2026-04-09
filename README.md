# 🚀 LaunchMind – Multi-Agent Startup System

LaunchMind is a fully autonomous Multi-Agent System (MAS) that takes a startup idea and runs it end-to-end: generating a product spec, building a real HTML landing page, committing it to GitHub, sending a cold outreach email, and posting a launch announcement to Slack — all without human intervention.

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
| **CEO** | Orchestrator | LLM task decomposition, output review, dynamic revision requests, final Slack summary |
| **Product** | PM | Generates value prop, personas, features, user stories |
| **Engineer** | Builder | Generates HTML, creates GitHub issue, commits to branch, opens PR |
| **Marketing** | Growth | Generates tagline/email/social copy, sends email via Gmail API, posts to Slack |
| **QA** | Reviewer | Reviews HTML + copy against spec, posts PR review comments, pass/fail verdict |

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

## 🔄 Dynamic Decision-Making

The CEO agent demonstrates autonomous decision-making in two ways:

1. **Product spec review**: After receiving the Product agent's spec, the CEO uses an LLM to evaluate whether it is specific enough. If not, it sends a `revision_request` with targeted feedback before proceeding.

2. **QA-triggered Engineer revision**: If the QA agent returns a `fail` verdict, the CEO instructs the Engineer agent to revise the HTML, addressing the specific issues raised — creating a real feedback loop.

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
  ├── llm_client.py        # Shared OpenAI-compatible client (OpenAI + Groq)
    ├── ceo_agent.py         # Orchestrator with LLM review + dynamic routing
    ├── product_agent.py     # Product spec generation
    ├── engineer_agent.py    # HTML generation + GitHub API
    ├── marketing_agent.py   # Copy generation + Gmail API + Slack
    └── qa_agent.py          # HTML/copy review + GitHub PR comments
```

---

## 🌐 Platform Integrations

| Platform | What the agent does |
|---|---|
| **OpenAI / Groq / Gemini APIs** | All 5 agents use a shared OpenAI-compatible LLM client. Provider is selected with `LLM_PROVIDER`. |
| **GitHub** | Engineer creates issue, commits `index.html` to a branch, opens PR. QA posts inline review comments. |
| **Slack** | Marketing posts a Block Kit launch announcement to `#launches`. CEO posts a final summary. |
| **Gmail API** | Marketing sends a cold outreach email to a test inbox. |

---

## 📤 Output Artifacts

After running, you'll find:

- `output/index.html` — The generated landing page
- `logs/ceo_decisions.json` — Every decision the CEO made and why
- `logs/message_bus.json` — Full audit trail of all inter-agent messages

---

## 🔗 Links

- **GitHub PR (Engineer):** *(will be filled after first run)*
- **Slack workspace:** *(add invite link here)*

---

## ❓ FAQ

**Q: Can I run without GitHub/Slack/Gmail API?**
Yes. Set only the selected LLM provider key (for example `OPENAI_API_KEY` when `LLM_PROVIDER=openai`, `GROQ_API_KEY` when `LLM_PROVIDER=groq`, or `GEMINI_API_KEY` when `LLM_PROVIDER=gemini`). The system will still generate all content locally and save `output/index.html` — it just won't perform real-world actions.

**Q: How do I show the full message history in the demo?**
```bash
cat logs/message_bus.json | python -m json.tool
```

**Q: How do I change the startup idea?**
```bash
python main.py --idea "Your idea here"
```
