import re
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.core.persona import GREETING_REPLY, HELP_MENU, get_persona_prompt
from agent.core.session import get_session
from agent.services.llm import get_llm

logger = get_logger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "router_classify.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text()

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


async def route_input(state: AgentState) -> AgentState:
    user_msg = (state.get("user_message") or "").strip()
    phone = state.get("whatsapp_phone", "")

    if not user_msg:
        return {**state, "intent": "general", "error": "No message provided"}

    # Continue multi-turn wizard if session is awaiting input
    if phone:
        session = await get_session(phone)
        awaiting = session.get("awaiting")
        if awaiting:
            logger.info(
                "Session continuation awaiting=%s for task %s",
                awaiting,
                state.get("task_id"),
            )
            updates: dict = {
                "session_awaiting": awaiting,
                "repo_provider": session.get("provider") or state.get("repo_provider", ""),
                "session_data": session.get("data") or {},
            }
            if awaiting.startswith("meeting"):
                updates["intent"] = "schedule_meeting"
            elif awaiting.startswith("repo") or awaiting in (
                "provider",
                "project",
                "repo",
                "code_instruction",
            ):
                updates["intent"] = "browse_repos"
            else:
                updates["intent"] = "browse_repos"
            return {**state, **updates}

    if is_simple_greeting(user_msg):
        logger.info("Router fast-path greeting for task %s", state.get("task_id"))
        return {
            **state,
            "intent": "general",
            "notification_text": GREETING_REPLY,
        }

    if _HELP_RE.match(user_msg):
        return {**state, "intent": "general", "notification_text": HELP_MENU}

    if user_msg.strip() in ("1",):
        return {**state, "intent": "check_email"}
    if user_msg.strip() in ("2",):
        return {**state, "intent": "schedule_meeting"}
    if user_msg.strip() in ("3",):
        return {**state, "intent": "browse_repos"}
    if user_msg.strip() in ("5",):
        return {**state, "intent": "task_status"}

    if _EMAILS_RE.match(user_msg):
        return {**state, "intent": "check_email"}

    if _REPOS_RE.match(user_msg):
        return {**state, "intent": "browse_repos"}

    if _STATUS_RE.match(user_msg):
        return {**state, "intent": "task_status"}

    if _MEETING_RE.search(user_msg):
        return {**state, "intent": "schedule_meeting"}

    llm = get_llm(temperature=0)
    t0 = time.perf_counter()
    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_msg),
    ])
    logger.info(
        "Router LLM classify took %.2fs for task %s",
        time.perf_counter() - t0,
        state.get("task_id"),
    )

    import json

    try:
        result = json.loads(response.content)
    except json.JSONDecodeError:
        raw = response.content
        start, end = raw.find("{"), raw.rfind("}") + 1
        try:
            result = json.loads(raw[start:end]) if start != -1 and end > 0 else {}
        except json.JSONDecodeError:
            logger.warning("Router failed to parse LLM response: %s", response.content)
            return {**state, "intent": "general"}

    intent = result.get("intent", "general")
    logger.info("Router classified intent=%s for task %s", intent, state.get("task_id"))

    updates = {"intent": intent}

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

    if intent == "schedule_meeting":
        if result.get("meeting_title"):
            updates["meeting_title"] = result["meeting_title"]
        if result.get("meeting_when"):
            updates["meeting_when"] = result["meeting_when"]
        if result.get("meeting_attendees"):
            updates["meeting_attendees"] = result["meeting_attendees"]

    if intent == "general":
        # Persona reply will be generated in notify_general unless prefilled
        pass

    return {**state, **updates}
