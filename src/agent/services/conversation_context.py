"""Enterprise multi-agent conversation context — pause/resume fabric flows.

Preserves pick lists, wizard steps, and workspace state when Sunny switches
between Knowledge, Dev, Release, Meeting, etc. mid-conversation.
"""

from __future__ import annotations

import time
from typing import Any

from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
from agent.workflow.gates import is_workflow_gate

logger = get_logger(__name__)

PAUSABLE_AWAITINGS = frozenset(
    {
        "doc_pick",
        "meeting_pick",
        "meeting_email_to",
        "meeting_board_project_pick",
        "boards_project_pick",
        "boards_ticket_pick",
        "provider",
        "project",
        "repo",
        "code_instruction",
        "meeting_title",
        "meeting_when",
        "meeting_attendees",
    }
)

_MAX_STACK = 5

_AWAITING_LABELS = {
    "doc_pick": "Document Knowledge (pick list)",
    "meeting_pick": "Meeting Intelligence (transcript pick)",
    "meeting_email_to": "Meeting plan email",
    "meeting_board_project_pick": "DevOps board project pick",
    "boards_project_pick": "Azure Boards project pick",
    "boards_ticket_pick": "Azure Boards ticket pick",
    "provider": "Repo provider wizard",
    "project": "Azure DevOps project pick",
    "repo": "Repository pick",
    "code_instruction": "Code change description",
    "meeting_title": "Calendar meeting title",
    "meeting_when": "Calendar meeting time",
    "meeting_attendees": "Calendar meeting attendees",
}


def label_for_awaiting(awaiting: str | None) -> str:
    return _AWAITING_LABELS.get(awaiting or "", awaiting or "conversation")


async def pause_current_context(phone: str | None, *, reason: str = "") -> dict[str, Any] | None:
    """Push the active pick/wizard step onto paused_contexts before a fabric switch."""
    if not phone:
        return None
    try:
        session = await get_session(phone)
    except Exception:
        logger.exception("pause_current_context get_session failed")
        return None

    awaiting = session.get("awaiting")
    if not awaiting or awaiting not in PAUSABLE_AWAITINGS:
        return None

    data = dict(session.get("data") or {})
    stack: list[dict[str, Any]] = list(data.get("paused_contexts") or [])
    entry = {
        "awaiting": awaiting,
        "workspace": (data.get("active_workspace") or "general"),
        "label": label_for_awaiting(awaiting),
        "reason": (reason or "")[:120],
        "saved_at": time.time(),
    }
    stack.append(entry)
    data["paused_contexts"] = stack[-_MAX_STACK:]
    try:
        await save_session(phone, data={"paused_contexts": data["paused_contexts"]}, merge_data=True)
    except Exception:
        logger.exception("Failed saving paused_contexts for %s", phone)
        return None
    logger.info("Paused context phone=%s awaiting=%s reason=%s", phone, awaiting, reason)
    return entry


async def soft_fabric_switch(
    phone: str | None,
    *,
    next_workspace: str,
    reason: str = "",
    clear_awaiting: bool = True,
) -> str:
    """Pause pick/wizard context, optionally clear awaiting, return handoff banner."""
    if phone:
        await pause_current_context(phone, reason=reason)
        try:
            from agent.services.workspace_handoff import handoff_note_for, mark_workspace

            note = await handoff_note_for(phone, next_workspace=next_workspace)
            await mark_workspace(phone, next_workspace)
            if clear_awaiting:
                await save_session(phone, awaiting=None, clear_awaiting=True, merge_data=True)
            resume_hint = await resume_hint_from_session(phone)
            parts = [p for p in (note, resume_hint) if p]
            return "\n".join(parts)
        except Exception:
            logger.exception("soft_fabric_switch failed phone=%s", phone)
    return ""


def resume_hint_for(phone: str | None = None) -> str:
    """Short hint when paused contexts exist (sync — pass stack from session data)."""
    return "_Say **back** or **resume previous** to return to a paused step._"


async def resume_hint_from_session(phone: str | None) -> str:
    if not phone:
        return ""
    try:
        session = await get_session(phone)
        stack = list((session.get("data") or {}).get("paused_contexts") or [])
        if not stack:
            return ""
        last = stack[-1]
        label = last.get("label") or label_for_awaiting(last.get("awaiting"))
        return f"_Paused: **{label}** — say **back** or **resume previous** to continue._"
    except Exception:
        return ""


async def resume_previous_context(phone: str | None) -> dict[str, Any] | None:
    """Pop and restore the most recent paused pick/wizard step."""
    if not phone:
        return None
    try:
        session = await get_session(phone)
    except Exception:
        logger.exception("resume_previous get_session failed")
        return None

    data = dict(session.get("data") or {})
    stack: list[dict[str, Any]] = list(data.get("paused_contexts") or [])
    if not stack:
        return None

    # Do not clobber an open deploy gate
    if is_workflow_gate(session.get("awaiting")):
        return {
            "restored": False,
            "message": (
                "*Cannot resume previous step* — a coding approval gate is still open.\n\n"
                "Reply *status* for the gate, *PROCEED/APPROVE* to continue, or *stop* to clear."
            ),
        }

    entry = stack.pop()
    awaiting = entry.get("awaiting")
    workspace = entry.get("workspace") or "general"
    label = entry.get("label") or label_for_awaiting(awaiting)

    await save_session(
        phone,
        awaiting=awaiting,
        data={
            "paused_contexts": stack,
            "active_workspace": workspace,
        },
        merge_data=True,
    )
    logger.info("Resumed context phone=%s awaiting=%s", phone, awaiting)

    hint = _resume_instruction(awaiting)
    return {
        "restored": True,
        "awaiting": awaiting,
        "workspace": workspace,
        "label": label,
        "message": (
            f"*Resumed:* **{label}**\n\n"
            f"{hint}\n\n"
            f"_Other paused steps: {len(stack)}. Say **back** again to step further back._"
            if stack
            else f"*Resumed:* **{label}**\n\n{hint}"
        ),
    }


def _resume_instruction(awaiting: str | None) -> str:
    if awaiting == "doc_pick":
        return "Your document catalog is still in session — reply with page numbers to *ingest*, or *list my documents* to refresh."
    if awaiting == "meeting_pick":
        return "Your meeting transcript list is still available — reply with numbers to *select meetings*, or *make a plan*."
    if awaiting in ("provider", "project", "repo", "code_instruction"):
        return "Continue the repo wizard — reply with your pick or describe the change."
    if awaiting == "boards_project_pick":
        return "Reply with the Azure Boards project number or name."
    if awaiting == "boards_ticket_pick":
        return "Reply with the work item number to start Dev on that ticket."
    return "Continue where you left off, or say *help* for commands."
