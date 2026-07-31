import uuid

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph

from agent.agents.deployment import deploy_code
from agent.agents.developer import develop_code
from agent.agents.gmail_agent import compose_and_send_email, read_gmail
from agent.agents.notification import notify
from agent.agents.reviewer import review_code
from agent.agents.router import route_input
from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

DB_PATH = settings.database_url.replace("sqlite+aiosqlite:///", "")


def _route_after_classify(state: AgentState) -> str:
    intent = state.get("intent", "")

    if intent == "check_email":
        return "read_gmail"
    if intent == "send_email":
        return "send_email"
    if intent in ("code_change", "bug_fix"):
        return "develop_code"
    if intent in ("approval_yes", "approval_no"):
        return "handle_approval"
    return "notify_general"


def _route_after_gmail(state: AgentState) -> str:
    intent = state.get("intent", "")
    if intent == "code_change":
        return "develop_code"
    return "notify_result"


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
    return "notify_result"


async def _request_approval(state: AgentState) -> AgentState:
    changes = state.get("file_changes", [])
    changes_text = "\n".join(f"- {c.get('action', 'modify')} `{c['path']}`" for c in changes)

    return {
        **state,
        "status": "awaiting_approval",
        "notification_text": changes_text or "Changes ready for review.",
        "approval_status": "pending",
    }


async def _handle_approval_response(state: AgentState) -> AgentState:
    return state


async def _notify_general(state: AgentState) -> AgentState:
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.services.llm import get_llm

    llm = get_llm(temperature=0.3)
    response = await llm.ainvoke([
        SystemMessage(content="You are a helpful AI assistant. Answer concisely."),
        HumanMessage(content=state.get("user_message", "Hello")),
    ])

    return {
        **state,
        "status": "general_response",
        "notification_text": response.content,
    }


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("route_input", route_input)
    graph.add_node("read_gmail", read_gmail)
    graph.add_node("develop_code", develop_code)
    graph.add_node("review_code", review_code)
    graph.add_node("request_approval", _request_approval)
    graph.add_node("notify_approval", notify)
    graph.add_node("handle_approval", _handle_approval_response)
    graph.add_node("deploy_code", deploy_code)
    graph.add_node("notify_result", notify)
    graph.add_node("notify_general", _notify_general)
    graph.add_node("notify_general_send", notify)
    graph.add_node("notify_gmail", notify)
    graph.add_node("notify_dev", notify)
    graph.add_node("send_email", compose_and_send_email)
    graph.add_node("notify_email_sent", notify)

    graph.set_entry_point("route_input")

    graph.add_conditional_edges("route_input", _route_after_classify, {
        "read_gmail": "read_gmail",
        "send_email": "send_email",
        "develop_code": "develop_code",
        "handle_approval": "handle_approval",
        "notify_general": "notify_general",
    })

    graph.add_edge("send_email", "notify_email_sent")
    graph.add_edge("notify_email_sent", END)

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
        "notify_result": "notify_result",
    })

    graph.add_edge("deploy_code", "notify_result")
    graph.add_edge("notify_result", END)
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

    graph = build_graph()

    async with AsyncSqliteSaver.from_conn_string(DB_PATH) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": task_id}}

        logger.info("Starting graph execution for task %s", task_id)

        async for event in compiled.astream(initial_state, config):
            node = list(event.keys())[0] if event else "unknown"
            logger.info("Task %s: completed node '%s'", task_id, node)

    logger.info("Graph execution completed for task %s", task_id)


async def resume_graph(task_id: str, approval_status: str, whatsapp_phone: str) -> None:
    graph = build_graph()

    async with AsyncSqliteSaver.from_conn_string(DB_PATH) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": task_id}}

        state_update: AgentState = {
            "task_id": task_id,
            "approval_status": approval_status,
            "whatsapp_phone": whatsapp_phone,
            "source": "whatsapp",
            "intent": "approval_yes" if approval_status == "approved" else "approval_no",
        }

        logger.info("Resuming graph for task %s with approval=%s", task_id, approval_status)

        async for event in compiled.astream(state_update, config):
            node = list(event.keys())[0] if event else "unknown"
            logger.info("Task %s (resumed): completed node '%s'", task_id, node)


async def run_gmail_flow(history_id: str) -> None:
    task_id = str(uuid.uuid4())[:8]

    from agent.services.whatsapp import send_message

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

    graph = build_graph()

    async with AsyncSqliteSaver.from_conn_string(DB_PATH) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": task_id}}

        logger.info("Starting Gmail-triggered flow, task %s, historyId=%s", task_id, history_id)

        async for event in compiled.astream(initial_state, config):
            node = list(event.keys())[0] if event else "unknown"
            logger.info("Task %s (gmail): completed node '%s'", task_id, node)
