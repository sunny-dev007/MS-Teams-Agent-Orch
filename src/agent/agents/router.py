import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.llm import get_llm

logger = get_logger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "router_classify.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text()


async def route_input(state: AgentState) -> AgentState:
    llm = get_llm(temperature=0)
    user_msg = state.get("user_message", "")

    if not user_msg:
        return {**state, "intent": "general", "error": "No message provided"}

    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_msg),
    ])

    try:
        result = json.loads(response.content)
    except json.JSONDecodeError:
        logger.warning("Router failed to parse LLM response: %s", response.content)
        return {**state, "intent": "general"}

    intent = result.get("intent", "general")
    logger.info("Router classified intent=%s for task %s", intent, state.get("task_id"))

    updates: dict = {"intent": intent}

    if result.get("repo_url"):
        updates["repo_url"] = result["repo_url"]

    if intent in ("approval_yes", "approval_no"):
        updates["approval_status"] = "approved" if intent == "approval_yes" else "rejected"
        if result.get("task_id"):
            updates["task_id"] = result["task_id"]

    if intent == "send_email":
        if result.get("email_to"):
            updates["email_to"] = result["email_to"]
        if result.get("email_subject"):
            updates["email_subject"] = result["email_subject"]
        if result.get("email_body"):
            updates["email_body"] = result["email_body"]

    return {**state, **updates}
