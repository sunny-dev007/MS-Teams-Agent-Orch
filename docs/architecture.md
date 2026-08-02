# Architecture — WhatsApp AI Agent Platform

## 1. System Overview

The WhatsApp AI Agent is a **webhook-driven, multi-agent automation platform** that uses WhatsApp as the primary user interface. It processes natural language commands, routes them through specialized AI agents, and executes actions across Gmail, GitHub, Azure DevOps, and Google Calendar — with human-in-the-loop approval for all code changes.

### Design Principles

- **Single entry point**: Every interaction starts from a WhatsApp message
- **Agent specialization**: Each domain (email, code, calendar) has a dedicated agent
- **Human-in-the-loop**: No destructive action (code push, PR merge) happens without explicit approval
- **Stateful orchestration**: LangGraph persists graph state across pauses, enabling async approval flows
- **Graceful degradation**: Fast-path responses for greetings/help bypass the full agent graph

---

## 2. High-Level Design (HLD)

### 2.1 System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          USER INTERFACE                                 │
│                                                                         │
│    WhatsApp (Personal Phone)          Web Portal (/portal)              │
└────────────┬──────────────────────────────┬─────────────────────────────┘
             │                              │
             ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        INGRESS LAYER (FastAPI)                          │
│                                                                         │
│  POST /webhooks/whatsapp    POST /webhooks/gmail    GET /health         │
│  (HMAC-SHA256 verified)     (Pub/Sub push)          GET /portal         │
│  (Phone allowlist)                                  GET /tasks/         │
└────────────┬──────────────────────────────┬─────────────────────────────┘
             │                              │
             ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATION LAYER (LangGraph)                      │
│                                                                         │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ Background   │  │ StateGraph       │  │ AsyncSqliteSaver         │  │
│  │ Task Runner  │──│ (12 agent nodes) │──│ (checkpoint persistence) │  │
│  └──────────────┘  └──────────────────┘  └──────────────────────────┘  │
│                           │                                             │
│  ┌────────────────────────┼─────────────────────────────────────────┐  │
│  │           ConversationSession (multi-turn state)                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         AGENT LAYER                                     │
│                                                                         │
│  Planner ─→ Email Agent ─→ Calendar Agent ─→ Repo Wizard               │
│             Developer   ─→ Reviewer        ─→ Deployer                  │
│             General     ─→ Evaluator       ─→ Notifier                  │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        SERVICE LAYER                                    │
│                                                                         │
│  Azure OpenAI   Gmail API    GitHub API    Azure DevOps API             │
│  (GPT-4o-mini)  (OAuth2)     (PAT)         (PAT)                       │
│                                                                         │
│  WhatsApp API   Calendar API  Git Ops      CI Gate / CI Watch           │
│  (Bearer)       (OAuth2)      (GitPython)  (Deploy locks)               │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         DATA LAYER                                      │
│                                                                         │
│  SQLite (agent.db)                                                      │
│  ├── tasks              — task lifecycle tracking                       │
│  ├── conversation_sessions — multi-turn state per phone number          │
│  ├── pending_ci_watches  — durable pipeline watchers                    │
│  └── langgraph checkpoints — graph state persistence                    │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Agent Orchestration Flows

**Flow 1: General Conversation**
```
WhatsApp → Planner → General Agent → Notifier → WhatsApp Reply
```

**Flow 2: Email Read**
```
WhatsApp → Planner → Email Agent (read 5 recent) → Evaluator → Notifier
```

**Flow 3: Email Send**
```
WhatsApp → Planner → Email Agent (LLM compose + Gmail send) → Evaluator → Notifier
```

**Flow 4: Schedule Meeting**
```
WhatsApp → Planner → Calendar Agent (slot fill → Google Calendar) → Notifier
```
Multi-turn: if missing info (time, attendees), asks follow-up questions.

**Flow 5: Browse Repositories**
```
WhatsApp → Planner → Repo Wizard (Provider → Project → Repo → Task) → Notifier
```
Multi-turn: walks through selection steps, persists state in ConversationSession.

**Flow 6: Code Development Pipeline (Full)**
```
WhatsApp → Planner → Repo Wizard → Developer → Reviewer ←→ (max 3 loops)
                                                    │
                                                    ▼
                                            Request Approval
                                                    │
                                              PAUSE (END)
                                                    │
                                        User sends APPROVE/REJECT
                                                    │
                                                    ▼
                                    Resume Graph → Deployer → CI Watch
                                                    │
                                                    ▼
                                                Evaluator → Final Summary
```

### 2.3 Key Architectural Patterns

| Pattern | Implementation |
|---------|---------------|
| **Event-driven** | WhatsApp/Gmail webhooks trigger background graph execution |
| **State machine** | LangGraph StateGraph with conditional edges for dynamic routing |
| **Human-in-the-loop** | Graph pauses at END, persists via checkpointer, resumes via `Command(goto=...)` |
| **Multi-turn sessions** | ConversationSession table tracks wizard steps and pending slots |
| **Graceful degradation** | Greeting/help fast-paths bypass graph to avoid dependency loading failures |
| **Provider isolation** | CI gate uses provider+repo keys for deploy locks |
| **Durable watchers** | PendingCiWatch rows survive app restarts, resume polling on lifespan startup |

---

## 3. Low-Level Design (LLD)

### 3.1 LangGraph State Machine

The state machine is defined in `src/agent/agents/graph.py`. Entry point: `planner`.

**Nodes (22 total):**

| Node | Function | Purpose |
|------|----------|---------|
| `planner` | `plan()` | Intent classification (regex + LLM) |
| `email_agent` | `run_email()` | Read Gmail inbox |
| `send_email_agent` | `run_send_email()` | Compose + send email |
| `calendar_agent` | `run_calendar()` | Schedule meetings |
| `repo_wizard` | `run_repo_wizard()` | Multi-turn repo browser |
| `task_status_agent` | `run_task_status()` | List recent tasks |
| `coding_developer` | `run_developer()` | LLM code generation |
| `coding_reviewer` | `run_reviewer()` | LLM code review |
| `coding_deployer` | `run_deployer()` | Push, PR, CI trigger |
| `general_agent` | `run_general()` | Free-form LLM chat |
| `evaluator` | `run_evaluator()` | Task summary generation |
| `request_approval` | `_request_approval()` | Format approval message |
| `handle_approval` | `_handle_approval()` | Process APPROVE/REJECT |
| `notify_*` (9 nodes) | `run_notifier()` | Status-specific WhatsApp messages |

**Conditional Routing Functions:**

| Function | After Node | Routes To |
|----------|-----------|-----------|
| `_route_after_plan` | planner | email / send_email / calendar / repos / code / approval / general |
| `_route_after_email` | email_agent | develop (actionable) or notify (summary only) |
| `_route_after_calendar` | calendar_agent | notify (done/failed) or ask (needs info) |
| `_route_after_repo_wizard` | repo_wizard | develop (has repo+task) or notify (listing) |
| `_route_after_developer` | notify_dev | reviewer (success) or evaluator (failed) |
| `_route_after_review` | coding_reviewer | approve (pass) / developer (loop) / notify (fail) |
| `_route_after_approval` | handle_approval | deployer (approved) or rejected notify |

### 3.2 AgentState Schema

```python
class AgentState(TypedDict, total=False):
    # Identity
    task_id: str                    # 8-char UUID prefix
    status: str                     # PENDING → task_started → awaiting_approval → deployed/failed
    source: str                     # whatsapp / gmail
    intent: str                     # Classified by planner

    # User input
    user_message: str               # Raw WhatsApp text

    # Email context
    email_to: str                   # Recipient for send
    email_subject: str
    email_body: str
    email_sender: str               # From address on read

    # Repository context
    repo_url: str                   # Full clone URL
    repo_owner: str
    repo_name: str
    branch_name: str                # Working branch

    # Developer output
    file_changes: list[dict]        # [{path, action, content}]
    dev_reasoning: str              # LLM explanation
    workspace_path: str             # Temp clone directory

    # Reviewer output
    review_result: str              # approved / changes_requested
    review_comments: str
    review_iteration: int           # Max 3

    # Approval
    approval_status: str            # pending / approved / rejected
    approval_message: str

    # Deployment output
    commit_sha: str
    pr_url: str
    pipeline_url: str
    pipeline_status: str

    # Communication
    whatsapp_phone: str
    notification_text: str
    messages: Annotated[list[BaseMessage], add_messages]
    error: str
```

### 3.3 Service Layer Detail

#### WhatsApp Service (`services/whatsapp.py`)
- **Base URL**: `https://graph.facebook.com/v21.0`
- **Auth**: Bearer token
- `send_message(phone, text)` — POST `/{phone_id}/messages`
- `check_access_token()` — GET `/{phone_id}` (validates token)
- `parse_incoming_message(body)` — extracts text, phone, message_id from webhook payload

#### Gmail Service (`services/gmail.py`)
- **Auth**: OAuth2 refresh token (scopes: `gmail.readonly`, `gmail.send`)
- `list_recent_messages(max_results, label)` — lists inbox
- `get_message(id)` — full email parse (headers + body)
- `send_email(to, subject, body)` — MIME multipart (text + HTML), proper From header with display name

#### GitHub Service (`services/github.py`)
- **Base URL**: `https://api.github.com`
- **Auth**: Bearer PAT + `X-GitHub-Api-Version: 2022-11-28`
- `create_pull_request()`, `merge_pull_request()`, `trigger_workflow()`, `list_repos()`, `list_active_workflow_runs()`

#### Azure DevOps Service (`services/azure_devops.py`)
- **Base URL**: `{org_url}/_apis/` (api-version 7.1)
- **Auth**: Basic `:PAT` base64
- `create_pull_request()`, `merge_pull_request()`, `trigger_pipeline()`, `push_commit()`, `download_repo_zip()`
- Safety: `trigger_pipeline()` and `find_pipeline_for_repo()` refuse cross-repo operations

#### Git Operations (`services/git_ops.py`)
- Primary: GitPython (`Repo.clone_from`, branch, commit, push)
- Fallback: HTTP API (GitHub zipball / AzDO zip download + AzDO Pushes API) when `git` binary is missing (common on App Service)

#### CI Gate (`services/ci_gate.py`)
- Provider-isolated async locks (key = `{provider}:{org}:{repo}`)
- `wait_for_ci_idle()` — checks GitHub active runs / AzDO active builds before allowing deploy
- `acquire_deploy_lock()` / `release_deploy_lock()` — prevents concurrent deploys

### 3.4 Database Schema

```
┌──────────────────────────┐
│         tasks            │
├──────────────────────────┤
│ id          VARCHAR(50)  │ PK
│ status      VARCHAR(50)  │
│ source      VARCHAR(20)  │
│ intent      VARCHAR(50)  │
│ repo_url    TEXT         │
│ branch_name VARCHAR(100) │
│ pr_url      TEXT         │
│ whatsapp_phone VARCHAR   │
│ error       TEXT         │
│ metadata_json TEXT       │
│ created_at  DATETIME     │
│ updated_at  DATETIME     │
└──────────────────────────┘

┌──────────────────────────────┐
│   conversation_sessions      │
├──────────────────────────────┤
│ phone       VARCHAR(30)      │ PK
│ awaiting    VARCHAR(50)      │
│ provider    VARCHAR(20)      │
│ data_json   TEXT             │
│ updated_at  DATETIME         │
└──────────────────────────────┘

┌──────────────────────────────┐
│     pending_ci_watches       │
├──────────────────────────────┤
│ id          INTEGER          │ PK AUTO
│ task_id     VARCHAR(50)      │
│ provider    VARCHAR(20)      │
│ org_url     TEXT             │
│ project     VARCHAR(200)     │
│ repo_name   VARCHAR(200)     │
│ build_id    VARCHAR(50)      │
│ pipeline_name VARCHAR(200)   │
│ phone       VARCHAR(30)      │
│ created_at  DATETIME         │
└──────────────────────────────┘
```

---

## 4. Sequence Diagrams

### 4.1 WhatsApp Message → Response

```
User          WhatsApp API      FastAPI           Background        LangGraph         Agent         WhatsApp API
  │                │               │                  │                │               │               │
  │──message──────→│               │                  │                │               │               │
  │                │──POST─────────→               │                │               │               │
  │                │               │──HMAC verify──→  │                │               │               │
  │                │               │──phone check──→  │                │               │               │
  │                │               │──BackgroundTask─→│                │               │               │
  │                │               │←─200 OK─────────│                │               │               │
  │                │               │                  │──"Working..."─→│               │           │
  │                │               │                  │──run_graph()──→│               │               │
  │                │               │                  │                │──plan()───────→│               │
  │                │               │                  │                │←─intent────────│               │
  │                │               │                  │                │──agent.run()──→│               │
  │                │               │                  │                │←─result────────│               │
  │                │               │                  │                │──notify()──────────────────────→│
  │                │←──────────────────────────────────────────────────────────────────────reply──────│
  │←──reply────────│               │                  │                │               │               │
```

### 4.2 Code Development with Approval

```
User          Planner      Developer    Reviewer      Approval      Deployer      CI Watch
  │              │              │            │             │             │             │
  │──"fix bug"──→│              │            │             │             │             │
  │              │──route───────→            │             │             │             │
  │              │              │──clone──→  │             │             │             │
  │              │              │──LLM───→   │             │             │             │
  │              │              │──apply──→  │             │             │             │
  │              │              │──notify──→ │             │             │             │
  │←─"changes ready"────────────│            │             │             │             │
  │              │              │────────────→             │             │             │
  │              │              │            │──review──→  │             │             │
  │              │              │            │←─approved   │             │             │
  │              │              │            │─────────────→             │             │
  │←─"APPROVE or REJECT?"──────────────────────────────── │             │             │
  │                                                       │             │             │
  │  ─── GRAPH PAUSES (state persisted to SQLite) ───     │             │             │
  │                                                       │             │             │
  │──"APPROVE abc123"─────────────────────────────────────→             │             │
  │              │              │            │             │──resume────→│             │
  │              │              │            │             │             │──push───→   │
  │              │              │            │             │             │──create PR─→│
  │              │              │            │             │             │──trigger CI→│
  │              │              │            │             │             │─────────────→│
  │              │              │            │             │             │             │──poll──→
  │←─"Deployed! PR: url"───────────────────────────────────────────────│             │
  │←─"Pipeline succeeded"──────────────────────────────────────────────────────────── │
```

---

## 5. Non-Functional Requirements

| Requirement | Implementation |
|-------------|---------------|
| **Availability** | Azure App Service with Always On; durable CI watchers survive restarts |
| **Latency** | Greeting/help fast-path < 500ms; full agent flow 3-15s depending on LLM |
| **Security** | HMAC webhook verification, phone allowlist, SecretStr for all tokens |
| **Scalability** | Single-user design; can scale by swapping SQLite for PostgreSQL + Celery |
| **Observability** | Structured logging with task_id correlation; /health endpoint with deep check |
| **Cost** | ~$5-18/month for personal usage |
| **Reliability** | Review loop max 3 iterations; CI gate prevents concurrent deploys |
