"""Enterprise multi-agent workspace handoff helpers.

Soft context switching between Knowledge (docs/QnA) and Dev (repos/coding)
without forcing destructive session clears — unless a workflow gate is active.
"""

from __future__ import annotations

from typing import Any

from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
from agent.workflow.gates import is_workflow_gate

logger = get_logger(__name__)

WS_KNOWLEDGE = "knowledge"
WS_DEV = "dev"
WS_GENERAL = "general"
WS_PRODUCTIVITY = "productivity"
WS_MEETING = "meeting"

_HANDOFF = {
    (WS_KNOWLEDGE, WS_DEV): (
        "_Workspace switch:_ Knowledge → **Dev**.\n"
        "Ingested documents stay in the knowledge base. "
        "Say *ask docs …* anytime to return.\n"
        "Coding approvals (PROCEED/APPROVE) are unchanged if a gate is open."
    ),
    (WS_DEV, WS_KNOWLEDGE): (
        "_Workspace switch:_ Dev → **Knowledge**.\n"
        "Repo/coding session context is left as-is unless a gate requires *stop*. "
        "Say *check my repos* to return to Dev."
    ),
    (WS_DEV, WS_PRODUCTIVITY): (
        "_Workspace switch:_ Dev → **Productivity** (Outlook / Boards).\n"
        "Coding gates stay open — say *status* or *stop* if needed."
    ),
    (WS_KNOWLEDGE, WS_PRODUCTIVITY): (
        "_Workspace switch:_ Knowledge → **Productivity** (Outlook / Boards).\n"
        "Say *ask docs …* anytime to return to Knowledge."
    ),
    (WS_PRODUCTIVITY, WS_DEV): (
        "_Workspace switch:_ Productivity → **Dev**.\n"
        "Say *check my outlook* or *my work items* to return."
    ),
    (WS_PRODUCTIVITY, WS_KNOWLEDGE): (
        "_Workspace switch:_ Productivity → **Knowledge**.\n"
        "Say *check my outlook* or *my work items* to return."
    ),
    (WS_KNOWLEDGE, WS_MEETING): (
        "_Workspace switch:_ Knowledge → **Meeting Intelligence**.\n"
        "Ingested docs stay available — say *ask docs …* anytime."
    ),
    (WS_MEETING, WS_KNOWLEDGE): (
        "_Workspace switch:_ Meeting Intelligence → **Knowledge**.\n"
        "Say *list my recent meetings* to return."
    ),
    (WS_MEETING, WS_DEV): (
        "_Workspace switch:_ Meeting Intelligence → **Dev**.\n"
        "Published plans remain in session — say *create devops board from plan* anytime."
    ),
    (WS_DEV, WS_MEETING): (
        "_Workspace switch:_ Dev → **Meeting Intelligence**.\n"
        "Coding gates stay open — say *status* or *stop* if needed."
    ),
}


async def mark_workspace(phone: str | None, workspace: str) -> None:
    if not phone:
        return
    try:
        await save_session(
            phone,
            data={"active_workspace": workspace},
            merge_data=True,
        )
    except Exception:
        logger.exception("Failed marking workspace=%s", workspace)


async def handoff_note_for(
    phone: str | None,
    *,
    next_workspace: str,
) -> str:
    """Return a soft professional handoff banner when workspaces change.

    Never auto-clears workflow gates (plan/PR/deploy) — those need explicit stop/approve.
    """
    if not phone:
        return ""
    try:
        session = await get_session(phone)
    except Exception:
        return ""
    data = session.get("data") or {}
    prev = (data.get("active_workspace") or "").strip() or WS_GENERAL
    if prev == next_workspace or prev == WS_GENERAL:
        return ""

    # If a coding gate is open and user jumps to knowledge — warn, don't clear
    awaiting = session.get("awaiting")
    extra = ""
    if next_workspace == WS_KNOWLEDGE and is_workflow_gate(awaiting):
        extra = (
            f"\n_Note:_ Dev gate `*{awaiting}*` is still open. "
            "Reply *status* to continue it, or *stop* to clear."
        )
    if next_workspace == WS_DEV and awaiting == "doc_pick":
        extra = "\n_Note:_ Document pick list is still available — say *ingest N* to resume."
    if next_workspace == WS_MEETING and awaiting == "doc_pick":
        extra = "\n_Note:_ Document pick list cleared — say *list my documents* to restore."
    if next_workspace == WS_KNOWLEDGE and awaiting == "meeting_pick":
        extra = "\n_Note:_ Meeting pick list is still available — say *select meetings N* or *make a plan*."

    note = _HANDOFF.get((prev, next_workspace), "")
    return (note + extra).strip()


def apply_handoff(state: dict[str, Any], note: str) -> dict[str, Any]:
    if not note:
        return state
    return {**state, "handoff_note": note}
