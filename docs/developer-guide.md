# Developer Guide

Deep-dive into the codebase: how the code flows, how agents work, how to extend the system.

---

## 1. Code Flow Walkthrough

### 1.1 Message Arrival → Response (End to End)

```
1. POST /webhooks/whatsapp
   └─ api/whatsapp.py: whatsapp_webhook()
      ├─ core/security.py: verify_whatsapp_signature()     # HMAC-SHA256
      ├─ core/security.py: is_phone_allowed()              # Allowlist check
      ├─ services/whatsapp.py: parse_incoming_message()     # Extract text, phone
      ├─ Regex match: APPROVE/REJECT pattern?
      │   ├─ YES → services/ci_gate.py: check busy
      │   │        agents/graph.py: resume_graph()          # Command(goto="handle_approval")
      │   └─ NO  → BackgroundTasks.add_task(handle_whatsapp_message)
      └─ Return {"status": "ok"} immediately

2. core/background.py: handle_whatsapp_message()
   ├─ planner/agent.py: is_simple_greeting()               # Regex fast-path
   │   └─ YES → services/whatsapp.py: send_message(GREETING_REPLY)
   │            Return (skip graph entirely)
   ├─ services/whatsapp.py: send_message("Working on it...") # Instant ack
   └─ agents/graph.py: run_graph()

3. agents/graph.py: run_graph()
   ├─ build_graph() → StateGraph with all nodes + edges
   ├─ AsyncSqliteSaver.from_conn_string()                   # Checkpointer
   ├─ graph.compile(checkpointer=checkpointer)
   └─ compiled.astream(initial_state, config)               # Stream execution
       └─ Each node completes → logged with task_id
```

### 1.2 Planner Decision Logic

The planner (`planner/agent.py:plan()`) follows this priority:

```
1. Check ConversationSession for in-progress multi-turn flow
   ├─ awaiting="approval" → remind about pending APPROVE/REJECT
   ├─ awaiting="meeting_*" → continue calendar slot filling
   ├─ awaiting="repo_*" → continue repo wizard step
   └─ awaiting="code_instruction" → start coding with provided text

2. Regex fast-paths (no LLM needed)
   ├─ "check email" / "inbox" → intent=check_email
   ├─ "send email" → intent=send_email
   ├─ "schedule meeting" / "set up a call" → intent=schedule_meeting
   ├─ "check repos" / "my repositories" → intent=browse_repos
   ├─ "task status" → intent=task_status
   └─ "help" → intent=general (returns HELP_MENU)

3. CI-busy check (if user might be trying to approve)
   └─ If CI running → notify "pipeline still running, please wait"

4. LLM classification (fallback)
   └─ Send user_message + router_classify.txt prompt
       → Parse JSON response for intent + extracted fields
```

### 1.3 Developer Agent Pipeline

```
develop_code(state) in agents/developer.py:
│
├─ 1. Parse repo URL → (owner, repo_name)
├─ 2. Clone repository
│     ├─ Primary: git_ops.clone_repo() via GitPython
│     └─ Fallback: HTTP zipball download (no git binary on App Service)
├─ 3. Create working branch: agent/task-{task_id}
├─ 4. Read repo tree: git_ops.get_repo_tree()
├─ 5. Build LLM prompt:
│     ├─ System: developer_system.txt (code expert persona)
│     └─ User: developer_codegen.txt template filled with:
│         ├─ task_description (from user_message)
│         ├─ repo_tree (file listing)
│         ├─ file_contents (key files content)
│         └─ review_feedback (if this is a revision loop)
├─ 6. Invoke LLM → parse JSON array of file changes
│     └─ [{path: "src/foo.py", action: "modify", content: "..."}]
├─ 7. Apply changes to workspace: git_ops.apply_changes()
└─ 8. Return state with file_changes, workspace_path, dev_reasoning
```

### 1.4 Review Loop

```
review_code(state) in agents/reviewer.py:
│
├─ Build review prompt:
│   ├─ System: reviewer_system.txt (senior reviewer persona)
│   └─ User: file changes + original task description
├─ Invoke LLM → parse JSON verdict:
│   {result: "approved"|"changes_requested", score: 1-10, issues: [...]}
├─ Soften false rejections (_soften_false_incomplete_html)
└─ Return state with review_result, review_comments, review_iteration++

Routing (_route_after_review):
├─ result="approved" → request_approval (ask human)
├─ result="changes_requested" AND iteration < 3 → coding_developer (loop)
└─ iteration >= 3 → request_approval (ask human anyway after max loops)
```

### 1.5 Approval & Deploy

```
Graph pauses after notify_approval → END (state saved in SQLite)

User sends "APPROVE abc123" via WhatsApp
│
├─ api/whatsapp.py: regex match → extract task_id
├─ services/ci_gate.py: check no CI running
├─ agents/graph.py: resume_graph()
│   └─ Command(update={approval_status: "approved"}, goto="handle_approval")
│
└─ handle_approval → _route_after_approval
    ├─ approved → coding_deployer
    │   ├─ git_ops.commit_and_push()
    │   ├─ github.create_pull_request() OR azure_devops.create_pull_request()
    │   ├─ Optionally: merge PR
    │   ├─ Optionally: trigger CI pipeline
    │   └─ ci_watch: start durable pipeline watcher
    └─ rejected → notify_rejected → evaluator
```

---

## 2. Architecture Layers

### 2.1 Two-Layer Agent Architecture

```
┌─────────────────────────────────────────────┐
│           LangGraph StateGraph              │
│                                             │
│   Registers SPECIALIST nodes                │
│   (specialists/*.py)                        │
│                                             │
│   Each specialist:                          │
│   ├─ Implements Specialist Protocol         │
│   ├─ Wraps an AGENT function               │
│   └─ Adds handled_by tag                   │
└──────────────────┬──────────────────────────┘
                   │ delegates to
                   ▼
┌─────────────────────────────────────────────┐
│           Agent Business Logic              │
│           (agents/*.py)                     │
│                                             │
│   Pure functions: (AgentState) → AgentState  │
│   ├─ Testable independently                │
│   ├─ No graph awareness                    │
│   └─ Call services directly                │
└─────────────────────────────────────────────┘
```

**Why two layers?**
- **Specialists** handle graph concerns (tagging, node registration)
- **Agents** contain pure business logic, testable without LangGraph
- Clean separation of concerns — agents don't know they're in a graph

### 2.2 Service Layer Pattern

Every external API has a dedicated service module:

```python
# services/github.py pattern:
def _headers() -> dict:
    """Auth headers — called per request."""
    return {"Authorization": f"Bearer {settings.github_token.get_secret_value()}"}

def create_pull_request(owner, repo, title, head, base, body=""):
    """Create a PR. Returns the PR URL."""
    resp = httpx.post(
        f"https://api.github.com/repos/{owner}/{repo}/pulls",
        headers=_headers(),
        json={"title": title, "head": head, "base": base, "body": body}
    )
    resp.raise_for_status()
    return resp.json()
```

Conventions:
- Auth credentials read from `settings` (never passed as arguments)
- Each function does one API call
- Errors propagate as exceptions (agents catch and set `error` in state)

---

## 3. Adding a New Agent

### Step 1: Create the agent logic

```python
# src/agent/agents/my_agent.py
from agent.agents.state import AgentState
from agent.core.logging import get_logger

logger = get_logger(__name__)

async def my_action(state: AgentState) -> AgentState:
    task_id = state.get("task_id", "unknown")
    user_msg = state.get("user_message", "")

    try:
        # Your logic here
        result = f"Processed: {user_msg}"

        return {
            **state,
            "status": "general_response",
            "notification_text": result,
        }
    except Exception as e:
        logger.exception("my_agent failed for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "error": str(e),
            "notification_text": f"Failed: {e}",
        }
```

### Step 2: Create the specialist wrapper

```python
# src/agent/specialists/my_specialist.py
from agent.agents.my_agent import my_action
from agent.agents.state import AgentState
from agent.specialists.base import tag

async def run_my_agent(state: AgentState) -> AgentState:
    result = await my_action(state)
    return tag(result, "my_agent")
```

### Step 3: Wire into the graph

In `src/agent/agents/graph.py`:

```python
from agent.specialists.my_specialist import run_my_agent

def build_graph():
    graph = StateGraph(AgentState)
    # ... existing nodes ...
    graph.add_node("my_agent", run_my_agent)
    graph.add_node("notify_my_agent", run_notifier)

    # Add to routing
    graph.add_conditional_edges("planner", _route_after_plan, {
        # ... existing routes ...
        "my_agent": "my_agent",
    })

    graph.add_edge("my_agent", "notify_my_agent")
    graph.add_edge("notify_my_agent", END)  # or → evaluator
```

### Step 4: Add intent to planner

In `src/agent/planner/agent.py`, add a regex fast-path or update `prompts/router_classify.txt` to include your new intent.

### Step 5: Write tests

```python
# tests/test_agents/test_my_agent.py
import pytest
from agent.agents.my_agent import my_action

@pytest.mark.asyncio
async def test_my_action():
    state = {"task_id": "test", "user_message": "hello", "status": "PENDING"}
    result = await my_action(state)
    assert result["status"] == "general_response"
```

---

## 4. LLM Prompt Engineering

### Prompt Files

| File | Used By | Purpose |
|------|---------|---------|
| `router_classify.txt` | Planner | Intent classification with JSON output |
| `developer_system.txt` | Developer | Expert coder persona |
| `developer_codegen.txt` | Developer | Template: task + repo tree + files → JSON changes |
| `reviewer_system.txt` | Reviewer | Senior reviewer persona with JSON verdict |
| `reviewer_checklist.txt` | Reviewer | Review criteria checklist |
| `persona_sunny.txt` | General Agent | Personal assistant persona with user context |

### Prompt Design Patterns

1. **JSON output enforcement**: Every classifying prompt ends with a JSON schema example
2. **Persona separation**: Developer and reviewer use different personas to get genuine reviews
3. **Template variables**: Codegen prompt uses `{task_description}`, `{repo_tree}`, `{file_contents}` placeholders
4. **Guard rails**: Reviewer prompt includes instructions to avoid false rejections on truncated content

### Modifying Prompts

Prompts are plain `.txt` files loaded at import time. Changes take effect on server restart. No code changes needed — just edit the file.

---

## 5. Key Patterns & Idioms

### Fast-Path Pattern
```python
# Avoid loading heavy dependencies for simple responses
if is_simple_greeting(message):
    await send_message(phone, GREETING_REPLY)
    return  # Skip graph entirely
```

### State Spread Pattern
```python
# Every agent returns full state with updates
return {
    **state,                              # Preserve all existing fields
    "status": "new_status",               # Override specific fields
    "notification_text": "Result text",
}
```

### Session-Aware Multi-Turn
```python
# Check for in-progress conversation
session = await get_session(phone)
if session and session.awaiting == "repo_selection":
    # Continue the wizard instead of starting fresh
    return handle_repo_selection(state, session)
```

### Provider Dispatch
```python
# Route to correct service based on repo URL
if "github.com" in repo_url:
    return await _deploy_github(state)
elif "dev.azure.com" in repo_url:
    return await _deploy_azdo(state)
```

---

## 6. Libraries Quick Reference

| Library | Import | Key Usage |
|---------|--------|-----------|
| FastAPI | `from fastapi import FastAPI, BackgroundTasks` | App, webhooks, background tasks |
| LangGraph | `from langgraph.graph import StateGraph, END` | Graph definition |
| LangGraph | `from langgraph.types import Command` | Graph resume with goto |
| LangGraph | `from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver` | State persistence |
| LangChain | `from langchain_core.messages import SystemMessage, HumanMessage` | LLM messages |
| LangChain | `from langchain_openai import AzureChatOpenAI` | Azure OpenAI wrapper |
| SQLAlchemy | `from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine` | Async DB |
| Google API | `from googleapiclient.discovery import build` | Gmail, Calendar clients |
| Google Auth | `from google.oauth2.credentials import Credentials` | OAuth2 token management |
| httpx | `import httpx` | HTTP client for REST APIs |
| GitPython | `from git import Repo` | Local git operations |
| Pydantic | `from pydantic_settings import BaseSettings` | Config from .env |
