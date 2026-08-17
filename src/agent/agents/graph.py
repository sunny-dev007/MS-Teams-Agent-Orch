"""LangGraph orchestration — planner routes; specialists execute in isolation."""

from __future__ import annotations

import uuid

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.planner.agent import plan
from agent.specialists.calendar_agent import run_calendar
from agent.specialists.coding_architect import run_architect
from agent.specialists.coding_deployer import run_deployer
from agent.specialists.coding_developer import run_developer
from agent.specialists.coding_reviewer import run_reviewer
from agent.specialists.docs_agent import run_docs
from agent.specialists.doc_library_agent import run_doc_library
from agent.specialists.doc_ingest_agent import run_doc_ingest
from agent.specialists.doc_rag_agent import run_doc_rag
from agent.specialists.doc_insights_agent import run_doc_insights
from agent.specialists.email_agent import run_email, run_send_email
from agent.specialists.evaluator_agent import run_evaluator, run_task_status
from agent.specialists.general_agent import run_general
from agent.specialists.notifier_agent import run_notifier
from agent.specialists.outlook_agent import run_outlook
from agent.specialists.boards_agent import run_boards
from agent.specialists.pr_publisher import run_pr_publisher
from agent.specialists.pr_reviewer import run_pr_reviewer
from agent.specialists.qa_agent import run_qa
from agent.specialists.repo_wizard import run_repo_wizard
from agent.workflow.gates import (
    multi_gate_enabled,
    persist_deploy_gate,
    persist_manual_pr_gate,
    persist_plan_gate,
    persist_pr_mode_gate,
)

logger = get_logger(__name__)

from agent.core.sqlite_paths import ensure_sqlite_file

# Ensure parent dir exists before LangGraph opens the checkpointer (Azure /home/site/data).
DB_PATH = str(ensure_sqlite_file(settings.database_url))

_checkpointer_cm = None
_checkpointer = None
_compiled = None


async def _get_compiled():
    global _checkpointer_cm, _checkpointer, _compiled, DB_PATH
    if _compiled is not None:
        return _compiled

    # Re-ensure on each cold compile — deploy may race before /home/site/data exists.
    DB_PATH = str(ensure_sqlite_file(settings.database_url))
    _checkpointer_cm = AsyncSqliteSaver.from_conn_string(DB_PATH)
    _checkpointer = await _checkpointer_cm.__aenter__()
    _compiled = build_graph().compile(checkpointer=_checkpointer)
    logger.info("Compiled planner→specialists graph (checkpoint=%s)", DB_PATH)
    return _compiled


async def close_graph_resources() -> None:
    global _checkpointer_cm, _checkpointer, _compiled
    if _checkpointer_cm is not None:
        try:
            await _checkpointer_cm.__aexit__(None, None, None)
        except Exception:
            logger.exception("Failed to close graph checkpointer")
    _checkpointer_cm = None
    _checkpointer = None
    _compiled = None


def _coding_entry_node() -> str:
    return "coding_architect" if multi_gate_enabled() else "coding_developer"


def _route_after_plan(state: AgentState) -> str:
    intent = state.get("intent", "")
    if intent == "check_email":
        return "email_agent"
    if intent == "send_email":
        return "send_email_agent"
    if intent == "schedule_meeting":
        return "calendar_agent"
    if intent == "browse_repos":
        return "repo_wizard"
    if intent == "task_status":
        return "task_status_agent"
    # Release Agent Fabric specialists (safe when flags off — agents return disabled text)
    if intent == "publish_release_notes":
        return "docs_agent"
    if intent == "run_qa":
        return "qa_agent"
    # Document Knowledge Fabric (safe when ENABLE_DOC_KNOWLEDGE=false)
    if intent == "list_docs":
        return "doc_library_agent"
    if intent == "ingest_docs":
        return "doc_ingest_agent"
    if intent == "ask_docs":
        return "doc_rag_agent"
    if intent == "summarize_docs":
        return "doc_insights_agent"
    if intent == "check_outlook":
        return "outlook_agent"
    if intent in ("list_boards", "boards_start_dev"):
        return "boards_agent"
    if intent in ("code_change", "bug_fix"):
        if not state.get("repo_url"):
            return "repo_wizard"
        return _coding_entry_node()
    if intent in ("approval_yes", "approval_no"):
        return "handle_approval"
    return "general_agent"


def _route_after_email(state: AgentState) -> str:
    if state.get("intent") == "code_change" and state.get("repo_url"):
        return "notify_then_architect" if multi_gate_enabled() else "notify_then_develop"
    return "notify_result"


def _route_after_repo_wizard(state: AgentState) -> str:
    if state.get("intent") == "code_change" and state.get("repo_url"):
        return "notify_then_architect" if multi_gate_enabled() else "notify_then_develop"
    return "notify_picker"


def _route_after_architect(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "notify_result"
    return "request_plan_approval"


def _route_after_notify_result(state: AgentState) -> str:
    """AzDO merge deploy: defer Final evaluation until CI watch completes."""
    if state.get("pipeline_status") == "watching":
        return END
    return "evaluator"


def _route_after_calendar(state: AgentState) -> str:
    if state.get("evaluation_text") or state.get("status") == "failed":
        return "notify_meeting"
    return "notify_meeting_ask"


def _route_after_developer(state: AgentState) -> str:
    """Skip review/approval when development failed (e.g. git missing)."""
    if state.get("status") == "failed":
        return "evaluator"
    return "coding_reviewer"


def _route_after_review(state: AgentState) -> str:
    result = (state.get("review_result") or "").lower().strip()
    iteration = state.get("review_iteration", 0)
    if state.get("status") == "failed" or not state.get("file_changes"):
        return "notify_result"
    if multi_gate_enabled():
        if result == "approved" or iteration >= 3:
            return "publish_pr"
        if result in ("rejected",):
            return "notify_result"
        return "coding_developer"
    if result == "approved":
        return "notify_then_approve"
    if iteration >= 3:
        return "notify_then_approve"
    if result in ("rejected",):
        return "notify_result"
    return "coding_developer"


def _route_after_pr_publish(state: AgentState) -> str:
    if state.get("status") == "failed" or not state.get("pr_url"):
        return "notify_result"
    return "request_pr_review_mode"


def _route_after_approval(state: AgentState) -> str:
    if state.get("approval_status") == "approved":
        return "coding_deployer"
    return "notify_rejected"


def _format_change_preview(file_changes: list, max_chars: int = 2800) -> str:
    """WhatsApp-friendly preview so Sunny can review without opening GitHub."""
    if not file_changes:
        return "No file changes listed."

    parts: list[str] = []
    used = 0
    for change in file_changes:
        action = change.get("action", "modify")
        path = change.get("path", "?")
        content = (change.get("content") or "").strip()
        header = f"*{action}* `{path}`"
        if not content:
            block = header
        else:
            # Keep preview readable on phone
            snippet = content if len(content) <= 900 else content[:900] + "\n…(truncated)"
            block = f"{header}\n```\n{snippet}\n```"
        if used + len(block) + 2 > max_chars:
            parts.append("_(more changes truncated — approve to deploy all)_")
            break
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)


async def _request_plan_approval(state: AgentState) -> AgentState:
    phone = state.get("whatsapp_phone", "")
    if phone:
        await persist_plan_gate(phone, state)
    return {
        **state,
        "status": "awaiting_plan_approval",
        "plan_approved": False,
        "approval_status": "pending",
    }


async def _request_pr_review_mode(state: AgentState) -> AgentState:
    phone = state.get("whatsapp_phone", "")
    if phone:
        await persist_pr_mode_gate(phone, state)
    provider = (state.get("repo_provider") or "github").replace("_", " ")
    return {
        **state,
        "status": "pr_created",
        "notification_text": (
            f"*Pull request:* {state.get('pr_url', 'N/A')}\n\n"
            "_CI may run on this PR as *validate only*. "
            "Live App Service deploy happens only after you *APPROVE* "
            "(merge to `main`)._\n\n"
            f"How should this PR be reviewed?\n"
            f"1. *AI review* — detailed score on WhatsApp\n"
            f"2. *Manual review* — review in {provider}\n\n"
            "Reply *1* / *AI REVIEW* or *2* / *MANUAL REVIEW*"
        ),
    }


async def _handle_plan_gate(state: AgentState) -> AgentState:
    if state.get("approval_status") == "rejected":
        return {
            **state,
            "status": "rejected",
            "notification_text": "Plan rejected. No code was changed.",
        }
    return {**state, "plan_approved": True, "status": "plan_approved"}


async def _handle_pr_review_choice(state: AgentState) -> AgentState:
    mode = (state.get("pr_review_mode") or "").lower().strip()
    if mode == "manual":
        phone = state.get("whatsapp_phone", "")
        if phone:
            await persist_manual_pr_gate(phone, state)
        return {
            **state,
            "status": "awaiting_manual_pr",
            "notification_text": (
                f"Open the PR and approve it in GitHub or Azure DevOps:\n"
                f"{state.get('pr_url', 'N/A')}\n\n"
                f"When done, reply *PR READY {state.get('task_id')}* "
                "for final deploy approval."
            ),
        }
    return {**state, "pr_review_mode": "ai"}


async def _handle_manual_pr_ready(state: AgentState) -> AgentState:
    return {**state, "status": "manual_pr_ready"}
async def _request_approval(state: AgentState) -> AgentState:
    changes = state.get("file_changes", [])
    preview = _format_change_preview(changes)
    phone = state.get("whatsapp_phone", "")
    task_id = state.get("task_id", "")
    review_result = (state.get("review_result") or "").lower().strip()
    if review_result == "changes_requested":
        preview = (
            "_Automated review had remaining comments, but you can still deploy._\n"
            f"{state.get('review_comments', '')}\n\n"
            f"{preview}"
        )
    if multi_gate_enabled() and state.get("pr_url"):
        preview = (
            f"*Pull request:* {state.get('pr_url')}\n"
            f"*PR review:* {state.get('pr_review_mode', 'ai')} "
            f"(score: {state.get('pr_review_score', 'N/A')})\n\n"
            f"{preview}"
        )
    # Persist deploy context so bare "Approve" / checkpoint loss still works
    if phone and task_id:
        await persist_deploy_gate(phone, state)
    return {
        **state,
        "status": "awaiting_approval",
        "notification_text": preview,
        "approval_status": "pending",
    }


async def _handle_approval(state: AgentState) -> AgentState:
    if state.get("approval_status") == "rejected":
        return {
            **state,
            "status": "rejected",
            "notification_text": "You rejected the changes. Nothing was pushed.",
        }
    return state


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Planner (routing only)
    graph.add_node("planner", plan)

    # Specialists
    graph.add_node("email_agent", run_email)
    graph.add_node("send_email_agent", run_send_email)
    graph.add_node("calendar_agent", run_calendar)
    graph.add_node("repo_wizard", run_repo_wizard)
    graph.add_node("task_status_agent", run_task_status)
    graph.add_node("docs_agent", run_docs)
    graph.add_node("qa_agent", run_qa)
    graph.add_node("doc_library_agent", run_doc_library)
    graph.add_node("doc_ingest_agent", run_doc_ingest)
    graph.add_node("doc_rag_agent", run_doc_rag)
    graph.add_node("doc_insights_agent", run_doc_insights)
    graph.add_node("outlook_agent", run_outlook)
    graph.add_node("boards_agent", run_boards)
    graph.add_node("coding_architect", run_architect)
    graph.add_node("coding_developer", run_developer)
    graph.add_node("coding_reviewer", run_reviewer)
    graph.add_node("publish_pr", run_pr_publisher)
    graph.add_node("pr_ai_reviewer", run_pr_reviewer)
    graph.add_node("coding_deployer", run_deployer)
    graph.add_node("general_agent", run_general)
    graph.add_node("evaluator", run_evaluator)

    # Notifier (WhatsApp I/O)
    graph.add_node("notify_result", run_notifier)
    graph.add_node("notify_picker", run_notifier)
    graph.add_node("notify_meeting", run_notifier)
    graph.add_node("notify_meeting_ask", run_notifier)
    graph.add_node("notify_general", run_notifier)
    graph.add_node("notify_dev", run_notifier)
    graph.add_node("notify_then_develop", run_notifier)
    graph.add_node("notify_then_architect", run_notifier)
    graph.add_node("notify_plan", run_notifier)
    graph.add_node("notify_pr_mode", run_notifier)
    graph.add_node("notify_manual_pr", run_notifier)
    graph.add_node("notify_pr_review", run_notifier)
    graph.add_node("notify_approval", run_notifier)
    graph.add_node("notify_review", run_notifier)
    graph.add_node("notify_rejected", run_notifier)
    graph.add_node("notify_email_sent", run_notifier)
    graph.add_node("notify_evaluation", run_notifier)

    graph.add_node("request_plan_approval", _request_plan_approval)
    graph.add_node("request_pr_review_mode", _request_pr_review_mode)
    graph.add_node("request_approval", _request_approval)
    graph.add_node("handle_plan_gate", _handle_plan_gate)
    graph.add_node("handle_pr_review_choice", _handle_pr_review_choice)
    graph.add_node("handle_manual_pr_ready", _handle_manual_pr_ready)
    graph.add_node("handle_approval", _handle_approval)

    graph.set_entry_point("planner")

    graph.add_conditional_edges("planner", _route_after_plan, {
        "email_agent": "email_agent",
        "send_email_agent": "send_email_agent",
        "calendar_agent": "calendar_agent",
        "repo_wizard": "repo_wizard",
        "task_status_agent": "task_status_agent",
        "docs_agent": "docs_agent",
        "qa_agent": "qa_agent",
        "doc_library_agent": "doc_library_agent",
        "doc_ingest_agent": "doc_ingest_agent",
        "doc_rag_agent": "doc_rag_agent",
        "doc_insights_agent": "doc_insights_agent",
        "outlook_agent": "outlook_agent",
        "boards_agent": "boards_agent",
        "coding_architect": "coding_architect",
        "coding_developer": "coding_developer",
        "handle_approval": "handle_approval",
        "general_agent": "general_agent",
    })

    graph.add_edge("send_email_agent", "notify_email_sent")
    graph.add_edge("notify_email_sent", "evaluator")

    graph.add_conditional_edges("calendar_agent", _route_after_calendar, {
        "notify_meeting": "notify_meeting",
        "notify_meeting_ask": "notify_meeting_ask",
    })
    graph.add_edge("notify_meeting", "evaluator")
    graph.add_edge("notify_meeting_ask", END)

    graph.add_edge("task_status_agent", "notify_result")
    graph.add_edge("docs_agent", "notify_result")
    graph.add_edge("qa_agent", "notify_result")
    graph.add_edge("doc_library_agent", "notify_result")
    graph.add_edge("doc_ingest_agent", "notify_result")
    graph.add_edge("doc_rag_agent", "notify_result")
    graph.add_edge("doc_insights_agent", "notify_result")
    graph.add_edge("outlook_agent", "notify_result")
    graph.add_edge("boards_agent", "notify_result")

    graph.add_conditional_edges("repo_wizard", _route_after_repo_wizard, {
        "notify_then_develop": "notify_then_develop",
        "notify_then_architect": "notify_then_architect",
        "notify_picker": "notify_picker",
    })
    graph.add_edge("notify_picker", END)
    graph.add_edge("notify_then_develop", "coding_developer")
    graph.add_edge("notify_then_architect", "coding_architect")

    graph.add_conditional_edges("coding_architect", _route_after_architect, {
        "request_plan_approval": "request_plan_approval",
        "notify_result": "notify_result",
    })
    graph.add_edge("request_plan_approval", "notify_plan")
    graph.add_edge("notify_plan", END)

    graph.add_conditional_edges("email_agent", _route_after_email, {
        "notify_then_develop": "notify_then_develop",
        "notify_then_architect": "notify_then_architect",
        "notify_result": "notify_result",
    })

    def _route_plan_gate(state: AgentState) -> str:
        if state.get("approval_status") == "rejected":
            return "notify_rejected"
        return "coding_developer"

    graph.add_conditional_edges("handle_plan_gate", _route_plan_gate, {
        "coding_developer": "coding_developer",
        "notify_rejected": "notify_rejected",
    })

    graph.add_edge("coding_developer", "notify_dev")
    graph.add_conditional_edges("notify_dev", _route_after_developer, {
        "coding_reviewer": "coding_reviewer",
        "evaluator": "evaluator",
    })

    graph.add_conditional_edges("coding_reviewer", _route_after_review, {
        "notify_then_approve": "notify_review",
        "publish_pr": "publish_pr",
        "coding_developer": "coding_developer",
        "notify_result": "notify_result",
    })

    graph.add_conditional_edges("publish_pr", _route_after_pr_publish, {
        "request_pr_review_mode": "request_pr_review_mode",
        "notify_result": "notify_result",
    })
    graph.add_edge("request_pr_review_mode", "notify_pr_mode")
    graph.add_edge("notify_pr_mode", END)

    def _route_pr_review_choice(state: AgentState) -> str:
        if (state.get("pr_review_mode") or "").lower() == "manual":
            return "notify_manual_pr"
        return "pr_ai_reviewer"

    graph.add_conditional_edges("handle_pr_review_choice", _route_pr_review_choice, {
        "pr_ai_reviewer": "pr_ai_reviewer",
        "notify_manual_pr": "notify_manual_pr",
    })
    graph.add_edge("notify_manual_pr", END)
    graph.add_edge("pr_ai_reviewer", "notify_pr_review")
    graph.add_edge("notify_pr_review", "request_approval")
    graph.add_edge("handle_manual_pr_ready", "request_approval")

    # Legacy path: internal review WhatsApp first, then deploy approval.
    graph.add_edge("notify_review", "request_approval")
    graph.add_edge("request_approval", "notify_approval")
    graph.add_edge("notify_approval", END)

    graph.add_conditional_edges("handle_approval", _route_after_approval, {
        "coding_deployer": "coding_deployer",
        "notify_rejected": "notify_rejected",
    })
    graph.add_edge("notify_rejected", "evaluator")
    graph.add_edge("coding_deployer", "notify_result")

    graph.add_conditional_edges("notify_result", _route_after_notify_result, {
        END: END,
        "evaluator": "evaluator",
    })
    graph.add_edge("evaluator", "notify_evaluation")
    graph.add_edge("notify_evaluation", END)

    graph.add_edge("general_agent", "notify_general")
    graph.add_edge("notify_general", END)

    return graph


async def run_graph(
    task_id: str,
    source: str,
    user_message: str,
    whatsapp_phone: str,
    repo_url: str = "",
) -> None:
    import time

    initial_state: AgentState = {
        "task_id": task_id,
        "status": "PENDING",
        "source": source,
        "user_message": user_message,
        "whatsapp_phone": whatsapp_phone,
        "repo_url": repo_url,
        "review_iteration": 0,
        "messages": [],
    }

    compiled = await _get_compiled()
    config = {"configurable": {"thread_id": task_id}}
    t0 = time.perf_counter()
    logger.info("Starting planner graph task=%s", task_id)

    async for event in compiled.astream(initial_state, config):
        node = list(event.keys())[0] if event else "unknown"
        logger.info("Task %s: completed node '%s'", task_id, node)

    logger.info("Task %s completed in %.2fs", task_id, time.perf_counter() - t0)


async def _ensure_deploy_feedback(
    phone: str,
    task_id: str,
    final: dict,
    *,
    gate: str,
) -> None:
    """Send WhatsApp if deploy resume ended in failure without a user-visible reason."""
    from agent.workflow.gates import GATE_DEPLOY
    from agent.services.deploy_notify import format_deploy_step_failed, send_deploy_progress

    if not phone or gate != GATE_DEPLOY:
        return
    if final.get("pipeline_status") == "watching":
        return
    if final.get("status") not in ("failed", "awaiting_approval"):
        return
    detail = (final.get("notification_text") or final.get("error") or "").strip()
    if not detail:
        detail = "Deploy did not finish — checkpoint or workspace may be missing on the server."
    await send_deploy_progress(
        phone,
        format_deploy_step_failed(task_id, "Deploy after approval", detail),
    )


async def resume_graph(
    task_id: str,
    approval_status: str,
    whatsapp_phone: str,
    *,
    gate: str = "deploy",
    pr_review_mode: str | None = None,
) -> None:
    from agent.core.session import clear_session, get_session
    from agent.services.git_ops import get_workspace_path
    from agent.workflow.gates import (
        GATE_DEPLOY,
        GATE_MANUAL_PR,
        GATE_PLAN,
        GATE_PR_MODE,
        hydrate_state_from_session,
    )

    compiled = await _get_compiled()
    config = {"configurable": {"thread_id": task_id}}

    session = await get_session(whatsapp_phone) if whatsapp_phone else {}
    pending = dict(session.get("data") or {})
    pending_id = pending.get("pending_task_id") or ""
    if pending_id and pending_id != task_id:
        pending = {}
    elif not pending_id or pending_id == task_id:
        pass

    state_update: AgentState = {
        "task_id": task_id,
        "approval_status": approval_status,
        "whatsapp_phone": whatsapp_phone,
        "source": "whatsapp",
        "intent": "approval_yes" if approval_status == "approved" else "approval_no",
    }

    hydrate_keys = (
        "workspace_path",
        "branch_name",
        "repo_url",
        "repo_owner",
        "repo_name",
        "repo_provider",
        "azdo_project",
        "azdo_repo_id",
        "user_message",
        "file_changes",
        "implementation_plan",
        "pr_url",
        "pr_number",
        "pr_id",
        "commit_sha",
        "review_comments",
        "review_result",
    )
    for key in hydrate_keys:
        if pending.get(key) is not None and pending.get(key) != "":
            state_update[key] = pending[key]

    if pr_review_mode:
        state_update["pr_review_mode"] = pr_review_mode

    if gate == GATE_PLAN:
        state_update["status"] = "rejected" if approval_status == "rejected" else "plan_approved"
        goto = "handle_plan_gate"
    elif gate == GATE_PR_MODE:
        state_update["status"] = "pr_review_choice"
        goto = "handle_pr_review_choice"
    elif gate == GATE_MANUAL_PR:
        state_update["status"] = "manual_pr_ready"
        goto = "handle_manual_pr_ready"
    else:
        state_update["status"] = "rejected" if approval_status == "rejected" else "deploying"
        goto = "handle_approval"

    try:
        snapshot = await compiled.aget_state(config)
        values = dict(snapshot.values or {})
    except Exception:
        values = {}

    for key, val in list(state_update.items()):
        if val not in (None, "", [], {}):
            values[key] = val
        elif key not in values and val is not None:
            values[key] = val

    if not values.get("workspace_path"):
        repo_dir = get_workspace_path(task_id) / "repo"
        if repo_dir.exists():
            values["workspace_path"] = str(repo_dir)
    if not values.get("branch_name"):
        values["branch_name"] = f"agent/{task_id}"

    values = await hydrate_state_from_session(values, session)

    needs_repo = gate in (GATE_PLAN, GATE_PR_MODE, GATE_MANUAL_PR, GATE_DEPLOY)
    if needs_repo and not values.get("repo_url") and not values.get("workspace_path"):
        from agent.workflow.resume_context import format_resume_failed
        from agent.services.whatsapp import send_message

        if whatsapp_phone:
            await send_message(
                whatsapp_phone,
                format_resume_failed(
                    task_id,
                    "Session expired or this task ran on another server instance.",
                ),
            )
        return

    logger.info(
        "Resuming task %s gate=%s approval=%s goto=%s",
        task_id,
        gate,
        approval_status,
        goto,
    )

    try:
        from langgraph.types import Command

        async for event in compiled.astream(
            Command(update=values, goto=goto),
            config,
        ):
            node = list(event.keys())[0] if event else "unknown"
            logger.info("Task %s (resumed): '%s'", task_id, node)

        if whatsapp_phone:
            try:
                snap = await compiled.aget_state(config)
                final = dict(snap.values or {})
            except Exception:
                final = {}
            # Safety net: after plan→dev→PR, always land session on pr_review_mode.
            if (
                gate == GATE_PLAN
                and approval_status == "approved"
                and final.get("pr_url")
            ):
                try:
                    from agent.workflow.gates import persist_pr_mode_gate

                    await persist_pr_mode_gate(whatsapp_phone, {**final, "whatsapp_phone": whatsapp_phone})
                except Exception:
                    logger.exception(
                        "Failed post-plan PR-mode persist for task %s", task_id
                    )
            # Keep WhatsApp / gate memory until user says STOP or check my repos.
            # Only plan *reject* clears automatically; deploy/CI must not wipe context.
            awaiting_after = (await get_session(whatsapp_phone)).get("awaiting")
            if gate == GATE_PLAN and approval_status == "rejected":
                await clear_session(whatsapp_phone)
            elif gate == GATE_DEPLOY and final.get("pipeline_status") == "watching":
                try:
                    from agent.workflow.gates import persist_pipeline_watching_gate

                    await persist_pipeline_watching_gate(
                        whatsapp_phone, {**final, "whatsapp_phone": whatsapp_phone}
                    )
                except Exception:
                    logger.exception(
                        "Failed persisting pipeline_watching gate for task %s", task_id
                    )
            elif gate == GATE_DEPLOY and awaiting_after:
                logger.info(
                    "Keeping session after deploy gate task=%s awaiting=%s",
                    task_id,
                    awaiting_after,
                )
            await _ensure_deploy_feedback(whatsapp_phone, task_id, final, gate=gate)
        return
    except Exception:
        logger.warning("Command resume unavailable; manual path", exc_info=True)
        if whatsapp_phone and gate == GATE_DEPLOY:
            from agent.services.deploy_notify import format_resume_deploy_failed, send_deploy_progress

            await send_deploy_progress(
                whatsapp_phone,
                format_resume_deploy_failed(
                    task_id,
                    "Resuming from saved session (graph checkpoint unavailable).",
                ),
            )

    # Fallback manual path (deploy gate only)
    if gate != GATE_DEPLOY:
        return

    values = dict(values)
    if approval_status == "approved":
        values = await run_deployer(values)
        values = await run_notifier(values)
        if values.get("error") != "ci_busy" and values.get("pipeline_status") not in (
            "busy",
            "watching",
        ):
            values = await run_evaluator(values)
            await run_notifier(values)
    else:
        values["status"] = "rejected"
        values["notification_text"] = "You rejected the changes. Nothing was pushed."
        values = await run_notifier(values)
        values = await run_evaluator(values)
        await run_notifier(values)

    # Keep session memory until STOP / check my repos (even after deploy path).
    if whatsapp_phone and values.get("pipeline_status") == "watching":
        try:
            from agent.workflow.gates import persist_pipeline_watching_gate

            await persist_pipeline_watching_gate(
                whatsapp_phone, {**values, "whatsapp_phone": whatsapp_phone}
            )
        except Exception:
            logger.exception(
                "Failed persisting pipeline_watching (fallback) for task %s", task_id
            )

    if whatsapp_phone:
        await _ensure_deploy_feedback(whatsapp_phone, task_id, values, gate=gate)


async def run_gmail_flow(history_id: str) -> None:
    task_id = str(uuid.uuid4())[:8]
    phone = settings.allowed_phone_numbers[0] if settings.allowed_phone_numbers else ""
    initial_state: AgentState = {
        "task_id": task_id,
        "status": "PENDING",
        "source": "gmail",
        "intent": "check_email",
        "user_message": f"Check emails (triggered by push, historyId={history_id})",
        "whatsapp_phone": phone,
        "review_iteration": 0,
        "messages": [],
    }
    compiled = await _get_compiled()
    config = {"configurable": {"thread_id": task_id}}
    # Jump into email specialist via planner-compatible state
    async for event in compiled.astream(initial_state, config):
        node = list(event.keys())[0] if event else "unknown"
        logger.info("Task %s (gmail): '%s'", task_id, node)
