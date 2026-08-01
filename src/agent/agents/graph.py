import uuid

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph

from agent.agents.deployment import deploy_code
from agent.agents.developer import develop_code
from agent.agents.evaluator import evaluate_result, task_status
from agent.agents.gmail_agent import compose_and_send_email, read_gmail
from agent.agents.meeting import schedule_meeting
from agent.agents.notification import notify
from agent.agents.repo_picker import browse_repos
from agent.agents.reviewer import review_code
from agent.agents.router import route_input
from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.core.persona import get_persona_prompt

logger = get_logger(__name__)

DB_PATH = settings.database_url.replace("sqlite+aiosqlite:///", "")

_checkpointer_cm = None
_checkpointer = None
_compiled = None


async def _get_compiled():
    global _checkpointer_cm, _checkpointer, _compiled
    if _compiled is not None:
        return _compiled

    _checkpointer_cm = AsyncSqliteSaver.from_conn_string(DB_PATH)
    _checkpointer = await _checkpointer_cm.__aenter__()
    _compiled = build_graph().compile(checkpointer=_checkpointer)
    logger.info("Compiled LangGraph with persistent SQLite checkpointer")
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


def _route_after_classify(state: AgentState) -> str:
    intent = state.get("intent", "")

    if intent == "check_email":
        return "read_gmail"
    if intent == "send_email":
        return "send_email"
    if intent == "schedule_meeting":
        return "schedule_meeting"
    if intent == "browse_repos":
        return "browse_repos"
    if intent == "task_status":
        return "task_status"
    if intent in ("code_change", "bug_fix"):
        if not state.get("repo_url"):
            return "browse_repos"
        return "develop_code"
    if intent in ("approval_yes", "approval_no"):
        return "handle_approval"
    return "notify_general"


def _route_after_gmail(state: AgentState) -> str:
    if state.get("intent") == "code_change" and state.get("repo_url"):
        return "develop_code"
    return "notify_result"


def _route_after_browse(state: AgentState) -> str:
    if state.get("intent") == "code_change" and state.get("repo_url"):
        return "start_dev_notify"
    return "notify_picker"


def _route_after_meeting(state: AgentState) -> str:
    if state.get("evaluation_text") or state.get("status") == "failed":
        return "notify_meeting"
    return "notify_meeting_ask"


def _route_after_review(state: AgentState) -> str:
    result = state.get("review_result", "")
    iteration = state.get("review_iteration", 0)

    if result == "approved":
        return "request_approval"
    if iteration >= 3:
        return "notify_result"
    return "develop_code"


def _route_after_approval(state: AgentState) -> str:
    status = state.get("approval_status", "")
    if status == "approved":
        return "deploy_code"
    return "notify_rejected"


async def _request_approval(state: AgentState) -> AgentState:
    changes = state.get("file_changes", [])
    changes_text = "\n".join(
        f"- {c.get('action', 'modify')} `{c['path']}`" for c in changes
    )
    return {
        **state,
        "status": "awaiting_approval",
        "notification_text": changes_text or "Changes ready for review.",
        "approval_status": "pending",
    }


async def _handle_approval_response(state: AgentState) -> AgentState:
    if state.get("approval_status") == "rejected":
        return {
            **state,
            "status": "rejected",
            "notification_text": "You rejected the changes. Nothing was pushed.",
        }
    return state


async def _notify_general(state: AgentState) -> AgentState:
    if state.get("notification_text"):
        return {**state, "status": "general_response"}

    import time

    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.services.llm import get_llm

    llm = get_llm(temperature=0.3)
    t0 = time.perf_counter()
    response = await llm.ainvoke([
        SystemMessage(
            content=(
                get_persona_prompt()
                + "\n\nAnswer concisely for WhatsApp. Prefer bullets over long paragraphs."
            )
        ),
        HumanMessage(content=state.get("user_message", "Hello")),
    ])
    logger.info(
        "General reply LLM took %.2fs for task %s",
        time.perf_counter() - t0,
        state.get("task_id"),
    )
    return {
        **state,
        "status": "general_response",
        "notification_text": response.content,
    }


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("route_input", route_input)
    graph.add_node("read_gmail", read_gmail)
    graph.add_node("schedule_meeting", schedule_meeting)
    graph.add_node("browse_repos", browse_repos)
    graph.add_node("task_status", task_status)
    graph.add_node("develop_code", develop_code)
    graph.add_node("review_code", review_code)
    graph.add_node("request_approval", _request_approval)
    graph.add_node("notify_approval", notify)
    graph.add_node("handle_approval", _handle_approval_response)
    graph.add_node("deploy_code", deploy_code)
    graph.add_node("notify_result", notify)
    graph.add_node("notify_rejected", notify)
    graph.add_node("notify_general", _notify_general)
    graph.add_node("notify_general_send", notify)
    graph.add_node("notify_gmail", notify)
    graph.add_node("notify_dev", notify)
    graph.add_node("start_dev_notify", notify)
    graph.add_node("send_email", compose_and_send_email)
    graph.add_node("notify_email_sent", notify)
    graph.add_node("notify_meeting", notify)
    graph.add_node("notify_meeting_ask", notify)
    graph.add_node("notify_picker", notify)
    graph.add_node("evaluate_result", evaluate_result)
    graph.add_node("notify_evaluation", notify)

    graph.set_entry_point("route_input")

    graph.add_conditional_edges("route_input", _route_after_classify, {
        "read_gmail": "read_gmail",
        "send_email": "send_email",
        "schedule_meeting": "schedule_meeting",
        "browse_repos": "browse_repos",
        "task_status": "task_status",
        "develop_code": "develop_code",
        "handle_approval": "handle_approval",
        "notify_general": "notify_general",
    })

    graph.add_edge("send_email", "notify_email_sent")
    graph.add_edge("notify_email_sent", "evaluate_result")

    graph.add_conditional_edges("schedule_meeting", _route_after_meeting, {
        "notify_meeting": "notify_meeting",
        "notify_meeting_ask": "notify_meeting_ask",
    })
    graph.add_edge("notify_meeting", "evaluate_result")
    graph.add_edge("notify_meeting_ask", END)

    graph.add_edge("task_status", "notify_result")

    graph.add_conditional_edges("browse_repos", _route_after_browse, {
        "start_dev_notify": "start_dev_notify",
        "notify_picker": "notify_picker",
    })
    graph.add_edge("notify_picker", END)
    graph.add_edge("start_dev_notify", "develop_code")

    graph.add_conditional_edges("read_gmail", _route_after_gmail, {
        "develop_code": "notify_gmail",
        "notify_result": "notify_result",
    })
    graph.add_edge("notify_gmail", "develop_code")

    graph.add_edge("develop_code", "notify_dev")
    graph.add_edge("notify_dev", "review_code")

    graph.add_conditional_edges("review_code", _route_after_review, {
        "request_approval": "request_approval",
        "develop_code": "develop_code",
        "notify_result": "notify_result",
    })

    graph.add_edge("request_approval", "notify_approval")
    graph.add_edge("notify_approval", END)

    graph.add_conditional_edges("handle_approval", _route_after_approval, {
        "deploy_code": "deploy_code",
        "notify_rejected": "notify_rejected",
    })
    graph.add_edge("notify_rejected", "evaluate_result")
    graph.add_edge("deploy_code", "notify_result")

    # notify_result used by several paths — then final evaluation
    graph.add_edge("notify_result", "evaluate_result")
    graph.add_edge("evaluate_result", "notify_evaluation")
    graph.add_edge("notify_evaluation", END)

    graph.add_edge("notify_general", "notify_general_send")
    graph.add_edge("notify_general_send", END)

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
    logger.info("Starting graph execution for task %s", task_id)

    async for event in compiled.astream(initial_state, config):
        node = list(event.keys())[0] if event else "unknown"
        logger.info("Task %s: completed node '%s'", task_id, node)

    logger.info(
        "Graph execution completed for task %s in %.2fs",
        task_id,
        time.perf_counter() - t0,
    )


async def resume_graph(task_id: str, approval_status: str, whatsapp_phone: str) -> None:
    compiled = await _get_compiled()
    config = {"configurable": {"thread_id": task_id}}

    state_update: AgentState = {
        "task_id": task_id,
        "approval_status": approval_status,
        "whatsapp_phone": whatsapp_phone,
        "source": "whatsapp",
        "intent": "approval_yes" if approval_status == "approved" else "approval_no",
        "status": "rejected" if approval_status == "rejected" else "deploying",
    }

    logger.info("Resuming graph for task %s with approval=%s", task_id, approval_status)

    try:
        from langgraph.types import Command

        async for event in compiled.astream(
            Command(update=state_update, goto="handle_approval"),
            config,
        ):
            node = list(event.keys())[0] if event else "unknown"
            logger.info("Task %s (resumed): completed node '%s'", task_id, node)
        return
    except Exception:
        logger.warning("Command resume unavailable; using manual deploy/reject path")

    # Manual continuation using checkpointed state values when possible
    snapshot = await compiled.aget_state(config)
    values = dict(snapshot.values or {})
    values.update(state_update)

    if approval_status == "approved":
        values = await deploy_code(values)
        values = await notify(values)
        values = await evaluate_result(values)
        await notify(values)
    else:
        values["status"] = "rejected"
        values["notification_text"] = "You rejected the changes. Nothing was pushed."
        values = await notify(values)
        values = await evaluate_result(values)
        await notify(values)


async def run_gmail_flow(history_id: str) -> None:
    task_id = str(uuid.uuid4())[:8]
    phone = settings.allowed_phone_numbers[0] if settings.allowed_phone_numbers else ""

    initial_state: AgentState = {
        "task_id": task_id,
        "status": "PENDING",
        "source": "gmail",
        "intent": "check_email",
        "user_message": f"Check emails (triggered by push notification, historyId={history_id})",
        "whatsapp_phone": phone,
        "review_iteration": 0,
        "messages": [],
    }

    compiled = await _get_compiled()
    config = {"configurable": {"thread_id": task_id}}
    logger.info("Starting Gmail-triggered flow, task %s, historyId=%s", task_id, history_id)

    async for event in compiled.astream(initial_state, config):
        node = list(event.keys())[0] if event else "unknown"
        logger.info("Task %s (gmail): completed node '%s'", task_id, node)
