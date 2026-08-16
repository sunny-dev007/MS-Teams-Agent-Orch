<div align="center">

# WhatsApp AI Agent

**Turn WhatsApp into a personal engineering command center.**

A multi-agent, LLM-powered automation platform that reads emails, schedules meetings, browses repositories, develops code, reviews changes, and deploys to production — all through conversational WhatsApp messages with human-in-the-loop approval.

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776ab?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent-1c3c3c?logo=langchain&logoColor=white)](https://github.com/langchain-ai/langgraph)
[![Azure OpenAI](https://img.shields.io/badge/Azure_OpenAI-GPT--4o-0078d4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/en-us/products/ai-services/openai-service)
[![Azure App Service](https://img.shields.io/badge/Deployed_on-Azure_App_Service-0078d4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/en-us/products/app-service)

[Architecture Docs](docs/architecture.md) · [E2E Operations](docs/E2E-OPERATIONS.md) · [Setup Guide](docs/setup-guide.md) · [API Reference](docs/api-reference.md) · [Developer Guide](docs/developer-guide.md) · [Release Notes](docs/RELEASE_NOTES.md)

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
infra/                      
```
