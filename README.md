<div align="center">

# WhatsApp AI Agent

**Turn WhatsApp into a personal engineering command center.**

A multi-agent, LLM-powered automation platform that reads emails, schedules meetings, browses repositories, develops code, reviews changes, and deploys to production — all through conversational WhatsApp messages with human-in-the-loop approval.

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776ab?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent-1c3c3c?logo=langchain&logoColor=white)](https://github.com/langchain-ai/langgraph)
[![Azure OpenAI](https://img.shields.io/badge/Azure_OpenAI-GPT--4o-0078d4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/en-us/products/ai-services/openai-service)
[![Azure App Service](https://img.shields.io/badge/Deployed_on-Azure_App_Service-0078d4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/en-us/products/app-service)

[Architecture Docs](docs/architecture.md) · [Setup Guide](docs/setup-guide.md) · [API Reference](docs/api-reference.md) · [Developer Guide](docs/developer-guide.md)

</div>

---

## What It Does

You send a WhatsApp message. AI agents handle the rest.

| You Say | What Happens |
|---------|-------------|
| *"check my emails"* | Reads Gmail, classifies actionable items, summarizes via WhatsApp |
| *"send email to alice@co.com about the meeting"* | LLM composes the email, sends via Gmail API |
| *"schedule a meeting tomorrow 3pm with bob@co.com"* | Creates Google Calendar event with Meet link |
| *"check my repos"* | Lists GitHub/Azure DevOps repos, lets you pick one |
| *"add error handling to the auth module"* | Clones repo, generates code via LLM, reviews it, asks for your approval |
| *"APPROVE abc123"* | Pushes code, creates PR, triggers CI/CD pipeline, monitors to completion |
| *"task status"* | Lists recent tasks with outcomes |

Every code change requires explicit **APPROVE** or **REJECT** via WhatsApp before anything is pushed.

---

## Architecture

```
WhatsApp ──→ FastAPI Webhook ──→ LangGraph State Machine ──→ Specialist Agents
                                        │
              ┌─────────────────────────┤
              ▼                         ▼
        Planner (Intent Router)    SQLite (State Persistence)
              │
    ┌─────────┼──────────┬──────────┬──────────┐
    ▼         ▼          ▼          ▼          ▼
  Email    Calendar    Repo      Developer   General
  Agent    Agent      Wizard    Agent       Agent
                                  │
                                  ▼
                              Reviewer ←──→ (max 3 iterations)
                                  │
                                  ▼
                          Request Approval ──→ PAUSE (WhatsApp)
                                  │
                          APPROVE / REJECT
                                  │
                                  ▼
                              Deployer ──→ PR + CI/CD ──→ Evaluator
```

**12 AI agents** orchestrated via LangGraph StateGraph with conditional routing, multi-turn conversation sessions, and durable checkpointing for human-in-the-loop approval.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Runtime** | Python 3.12, FastAPI, Uvicorn |
| **AI/LLM** | Azure OpenAI GPT-4o-mini, LangChain, LangGraph |
| **Messaging** | WhatsApp Cloud API (Meta Graph API v21) |
| **Email** | Gmail API (OAuth2 — read + send) |
| **Calendar** | Google Calendar API (event creation + Meet links) |
| **Source Control** | GitHub REST API, Azure DevOps REST API v7.1 |
| **Git** | GitPython with HTTP API fallback |
| **Database** | SQLite + aiosqlite + SQLAlchemy 2.0 (async) |
| **CI/CD** | Azure DevOps Pipelines (Oryx remote build) |
| **Hosting** | Azure App Service (B1 Linux, Central India) |

---

## Project Structure

```
src/agent/
├── main.py                 # FastAPI app, lifespan, middleware
├── config.py               # Pydantic Settings (.env loader)
├── api/                    # HTTP endpoints (webhooks, health, portal)
├── agents/                 # Business logic (developer, reviewer, gmail, etc.)
├── planner/                # Intent classification (regex + LLM)
├── specialists/            # Graph-wired agent wrappers
├── services/               # External API clients (WhatsApp, Gmail, GitHub, AzDO)
├── core/                   # Security, OAuth, sessions, background tasks
├── models/                 # SQLAlchemy ORM (Task, Session, CiWatch)
├── prompts/                # LLM prompt templates
└── web/                    # Portal HTML

docs/                       # Architecture, setup, API, developer guides
tests/                      # Unit + integration tests
scripts/                    # OAuth setup, token management
infra/                      # Azure deploy pipeline, startup script
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Azure OpenAI resource
- Meta Developer account (WhatsApp Business API)
- Google Cloud project (Gmail + Calendar APIs enabled)

### Install & Run

```bash
git clone https://dev.azure.com/Az-FullStack/Project-NIT/_git/web.Whatsapp-AI-Agent
cd web.Whatsapp-AI-Agent

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Fill in credentials — see docs/setup-guide.md

uvicorn agent.main:app --host 0.0.0.0 --port 8000
```

### Gmail OAuth (one-time)

```bash
python scripts/setup_gmail_oauth.py --credentials /path/to/google_creds.json
# Copy the printed refresh token into .env
```

### Verify

```bash
curl http://localhost:8000/health
# {"status":"healthy","version":"0.1.0"}
```

See the full [Setup Guide](docs/setup-guide.md) for WhatsApp webhook configuration, ngrok tunneling, and Azure deployment.

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture & HLD/LLD](docs/architecture.md) | System architecture, agent orchestration flows, state machine design, data models |
| [Setup Guide](docs/setup-guide.md) | Step-by-step local dev setup, credential generation, WhatsApp/Gmail configuration |
| [API Reference](docs/api-reference.md) | All HTTP endpoints, request/response formats, webhook payloads |
| [Developer Guide](docs/developer-guide.md) | Code flow walkthrough, library reference, adding new agents, prompt engineering |
| [Deployment Guide](docs/deployment.md) | Azure App Service deployment, CI/CD pipeline, environment variables |
| [Troubleshooting](docs/troubleshooting.md) | Common errors and fixes |

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **LangGraph** over raw orchestration | Built-in state persistence, conditional routing, graph visualization for demos |
| **SQLite** over PostgreSQL | Zero cost/setup for single-user POC; one-line swap to Postgres later |
| **BackgroundTasks** over Celery | No message broker needed for single-user workload |
| **Specialists + Agents** two-layer | Thin graph-wired wrappers keep business logic testable independently |
| **Oryx remote build** for deploy | Avoids glibc mismatch between CI Ubuntu and App Service Debian |
| **Phone allowlist** | Personal agent — only the owner's number is accepted |
| **Human-in-the-loop** | No code ships without explicit WhatsApp approval |

---

## Security

- **Webhook HMAC-SHA256** — every WhatsApp POST is signature-verified
- **Phone allowlist** — only configured numbers can interact
- **OAuth2** — Gmail/Calendar use refresh tokens (no passwords stored)
- **SecretStr** — all tokens wrapped in Pydantic SecretStr, never logged
- **CI Gate** — provider-isolated deploy locks prevent concurrent pushes
- **Human approval** — explicit APPROVE/REJECT required before any code push

---

## Cost

| Service | Monthly Cost |
|---------|-------------|
| Azure App Service (B1) | ~$13 |
| Azure OpenAI (GPT-4o-mini) | ~$2-5 |
| WhatsApp, Gmail, Calendar | Free tier |
| Azure DevOps Pipelines | Free tier |
| **Total** | **~$5-18** |

---

## Testing

```bash
PYTHONPATH=src python -m pytest tests/ -v
```

Covers: webhook verification, approval routing, CI gate locks, pipeline isolation, greeting fast-paths, payload parsing.

---

## License

Private project. All rights reserved.

---

<div align="center">
  <sub>Built by <a href="https://linkedin.com/in/sunny-kushwaha-genai">Sunny Kushwaha</a> — Senior Lead Engineer, GenAI & Distributed Systems</sub>
</div>
