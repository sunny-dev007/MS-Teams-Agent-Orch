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
from agent.services.llm import invoke_llm

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
_RELEASE_NOTES_RE = re.compile(
    r"(release\s*notes|write\s+(?:the\s+)?docs?|publish\s+(?:to\s+)?sharepoint|"
    r"documentation\s+agent|create\s+(?:a\s+)?(?:release\s+)?document)",
    re.IGNORECASE,
)
_RUN_QA_RE = re.compile(
    r"(run\s+qa|start\s+qa|qa\s+agent|playwright|test\s+(?:this\s+)?(?:release|deploy))",
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

    # Release Agent Fabric — take priority over in-progress repo wizard / coding session
    # so "write release notes for PR N" always goes to Docs Agent → SharePoint, not a code PR.
    if _RELEASE_NOTES_RE.search(user_msg):
        if phone:
            try:
                from agent.core.session import save_session

                await save_session(phone, awaiting=None, clear_awaiting=True, merge_data=True)
            except Exception:
                logger.exception("%s failed clearing session for docs intent", AGENT_NAME)
        return {**state, "intent": "publish_release_notes", "planned_by": AGENT_NAME}
    if _RUN_QA_RE.search(user_msg):
        if phone:
            try:
                from agent.core.session import save_session

                await save_session(phone, awaiting=None, clear_awaiting=True, merge_data=True)
            except Exception:
                logger.exception("%s failed clearing session for qa intent", AGENT_NAME)
        return {**state, "intent": "run_qa", "planned_by": AGENT_NAME}

    if phone:
        session = await get_session(phone)
        awaiting = session.get("awaiting")
        if awaiting == "approval":
            # Bare approve/reject should be handled in the WhatsApp webhook.
            lower = user_msg.lower().strip()
            tid = (session.get("data") or {}).get("pending_task_id") or "unknown"
            if lower.startswith("approve") or lower.startswith("reject"):
                return {
                    **state,
                    "intent": "general",
                    "notification_text": (
                        f"Please reply *APPROVE {tid}* or *REJECT {tid}* "
                        "(or just *Approve* / *Reject*)."
                    ),
                    "planned_by": AGENT_NAME,
                }
            # fall through to CI check below
        elif awaiting == "plan_approval":
            from agent.workflow.resume_context import format_gate_hint

            return {
                **state,
                "intent": "general",
                "notification_text": format_gate_hint(awaiting, session.get("data")),
                "planned_by": AGENT_NAME,
            }
        elif awaiting == "pr_review_mode":
            from agent.workflow.resume_context import format_gate_hint

            return {
                **state,
                "intent": "general",
                "notification_text": format_gate_hint(awaiting, session.get("data")),
                "planned_by": AGENT_NAME,
            }
        elif awaiting == "manual_pr_review":
            from agent.workflow.resume_context import format_gate_hint

            return {
                **state,
                "intent": "general",
                "notification_text": format_gate_hint(awaiting, session.get("data")),
                "planned_by": AGENT_NAME,
            }

        if awaiting == "approval":
            try:
                from agent.services.ci_gate import (
                    check_ci_busy_for_context,
                    context_from_session_data,
                )

                busy = await check_ci_busy_for_context(
                    context_from_session_data(
                        {
                            **(session.get("data") or {}),
                            "repo_provider": session.get("provider")
                            or (session.get("data") or {}).get("repo_provider", ""),
                        }
                    )
                )
                if busy.busy:
                    return {
                        **state,
                        "intent": "general",
                        "notification_text": busy.wait_message()
                        + f"\n\nPending approval task: `{tid}` — try *Approve* after CI finishes.",
                        "planned_by": AGENT_NAME,
                    }
            except Exception:
                logger.exception("%s CI check during approval wait failed", AGENT_NAME)

            return {
                **state,
                "intent": "general",
                "notification_text": (
                    f"You have a coding change waiting.\n"
                    f"Reply *APPROVE {tid}* "
                    f"or *REJECT …* to continue."
                ),
                "planned_by": AGENT_NAME,
            }
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

    t0 = time.perf_counter()
    try:
        response = await invoke_llm(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_msg),
            ],
            temperature=0,
            role="default",
        )
    except Exception:
        logger.exception("%s LLM classify failed task=%s — using keyword fallback", AGENT_NAME, state.get("task_id"))
        return {**state, "intent": "general", "planned_by": AGENT_NAME}

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
        # Never start an empty deploy from phrases like "please go ahead"
        # unless WhatsApp session actually has a pending gate.
        pending = await get_session(phone) if phone else {"awaiting": None, "data": {}}
        awaiting = pending.get("awaiting")
        if awaiting == "plan_approval":
            tid = result.get("task_id") or (pending.get("data") or {}).get("pending_task_id")
            return {
                **state,
                "intent": "general",
                "notification_text": (
                    "You still have an *implementation plan* waiting.\n"
                    f"Reply *PROCEED {tid or '<task_id>'}* to start development, "
                    f"or *REJECT {tid or '<task_id>'}* to cancel."
                ),
                "planned_by": AGENT_NAME,
            }
        if awaiting != "approval":
            return {
                **state,
                "intent": "general",
                "notification_text": (
                    "I don't have a pending coding change waiting for approval.\n\n"
                    "• After a *plan*, reply *PROCEED <task_id>*\n"
                    "• After review, reply *APPROVE <task_id>* to deploy\n"
                    "• Or say *check my repos* to start a new change"
                ),
                "planned_by": AGENT_NAME,
            }
        updates["approval_status"] = "approved" if intent == "approval_yes" else "rejected"
        tid = result.get("task_id") or (pending.get("data") or {}).get("pending_task_id")
        if tid:
            updates["task_id"] = tid
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
