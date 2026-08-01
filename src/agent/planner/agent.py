"""Planner agent: classify intent and extract slots. No I/O side effects."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.core.persona import GREETING_REPLY, HELP_MENU
from agent.core.session import get_session
from agent.services.llm import get_llm

logger = get_logger(__name__)

AGENT_NAME = "planner"

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "router_classify.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|hola|namaste|yo|good\s+(morning|afternoon|evening))"
    r"[\s!.?,]*$",
    re.IGNORECASE,
)
_HELP_RE = re.compile(r"^(help|menu|\?|commands)[\s!.?]*$", re.IGNORECASE)
_EMAILS_RE = re.compile(
    r"^(emails?|check\s+(my\s+)?(emails?|inbox)|inbox)[\s!.?]*$",
    re.IGNORECASE,
)
_REPOS_RE = re.compile(
    r"^(repos?|repositories|check\s+(my\s+)?repos?|browse\s+repos?)[\s!.?]*$",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"^(status|task\s+status|my\s+tasks?)[\s!.?]*$",
    re.IGNORECASE,
)
_MEETING_RE = re.compile(
    r"(schedule|set\s*up|setup|book).*(meeting|call|calendar)|meeting.*(schedule|set\s*up|book)",
    re.IGNORECASE,
)


def is_simple_greeting(message: str) -> bool:
    return bool(_GREETING_RE.match((message or "").strip()))


async def plan(state: AgentState) -> AgentState:
    """Route Sunny's WhatsApp message to exactly one specialist intent."""
    user_msg = (state.get("user_message") or "").strip()
    phone = state.get("whatsapp_phone", "")

    if not user_msg:
        return {**state, "intent": "general", "error": "No message provided", "planned_by": AGENT_NAME}

    if phone:
        session = await get_session(phone)
        awaiting = session.get("awaiting")
        if awaiting:
            logger.info("%s: continue awaiting=%s task=%s", AGENT_NAME, awaiting, state.get("task_id"))
            updates: dict = {
                "session_awaiting": awaiting,
                "repo_provider": session.get("provider") or state.get("repo_provider", ""),
                "session_data": session.get("data") or {},
                "planned_by": AGENT_NAME,
            }
            if str(awaiting).startswith("meeting"):
                updates["intent"] = "schedule_meeting"
            else:
                updates["intent"] = "browse_repos"
            return {**state, **updates}

    if is_simple_greeting(user_msg):
        return {
            **state,
            "intent": "general",
            "notification_text": GREETING_REPLY,
            "planned_by": AGENT_NAME,
        }

    if _HELP_RE.match(user_msg):
        return {
            **state,
            "intent": "general",
            "notification_text": HELP_MENU,
            "planned_by": AGENT_NAME,
        }

    if user_msg.strip() == "1" or _EMAILS_RE.match(user_msg):
        return {**state, "intent": "check_email", "planned_by": AGENT_NAME}
    if user_msg.strip() == "2" or _MEETING_RE.search(user_msg):
        return {**state, "intent": "schedule_meeting", "planned_by": AGENT_NAME}
    if user_msg.strip() == "3" or _REPOS_RE.match(user_msg):
        return {**state, "intent": "browse_repos", "planned_by": AGENT_NAME}
    if user_msg.strip() == "5" or _STATUS_RE.match(user_msg):
        return {**state, "intent": "task_status", "planned_by": AGENT_NAME}

    llm = get_llm(temperature=0)
    t0 = time.perf_counter()
    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_msg),
    ])
    logger.info("%s LLM classify %.2fs task=%s", AGENT_NAME, time.perf_counter() - t0, state.get("task_id"))

    try:
        result = json.loads(response.content)
    except json.JSONDecodeError:
        raw = response.content or ""
        start, end = raw.find("{"), raw.rfind("}") + 1
        try:
            result = json.loads(raw[start:end]) if start != -1 and end > 0 else {}
        except json.JSONDecodeError:
            logger.warning("%s parse failed: %s", AGENT_NAME, response.content)
            return {**state, "intent": "general", "planned_by": AGENT_NAME}

    intent = result.get("intent", "general")
    updates: dict = {"intent": intent, "planned_by": AGENT_NAME}

    if result.get("repo_url"):
        updates["repo_url"] = result["repo_url"]
    if intent in ("approval_yes", "approval_no"):
        updates["approval_status"] = "approved" if intent == "approval_yes" else "rejected"
        if result.get("task_id"):
            updates["task_id"] = result["task_id"]
    if intent == "send_email":
        for key in ("email_to", "email_subject", "email_body"):
            if result.get(key):
                updates[key] = result[key]
    if intent == "schedule_meeting":
        for key in ("meeting_title", "meeting_when", "meeting_attendees"):
            if result.get(key):
                updates[key] = result[key]

    logger.info("%s -> intent=%s task=%s", AGENT_NAME, intent, state.get("task_id"))
    return {**state, **updates}


# LangGraph node alias
route_input = plan
