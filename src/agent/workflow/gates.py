"""WhatsApp workflow gate helpers — plan, PR review mode, manual PR, deploy."""

from __future__ import annotations

from typing import Any

from agent.config import settings
from agent.core.logging import get_logger
from agent.core.session import clear_session, save_session

logger = get_logger(__name__)

GATE_PLAN = "plan_approval"
GATE_PR_MODE = "pr_review_mode"
GATE_MANUAL_PR = "manual_pr_review"
GATE_DEPLOY = "approval"


def multi_gate_enabled() -> bool:
    return bool(settings.enable_multi_gate_workflow)


async def persist_plan_gate(phone: str, state: dict[str, Any]) -> None:
    await save_session(
        phone,
        awaiting=GATE_PLAN,
        provider=state.get("repo_provider") or "",
        data=_gate_payload(state),
        merge_data=False,
    )


async def persist_pr_mode_gate(phone: str, state: dict[str, Any]) -> None:
    await save_session(
        phone,
        awaiting=GATE_PR_MODE,
        provider=state.get("repo_provider") or "",
        data=_gate_payload(state),
        merge_data=False,
    )


async def persist_manual_pr_gate(phone: str, state: dict[str, Any]) -> None:
    await save_session(
        phone,
        awaiting=GATE_MANUAL_PR,
        provider=state.get("repo_provider") or "",
        data=_gate_payload(state),
        merge_data=False,
    )


async def persist_deploy_gate(phone: str, state: dict[str, Any]) -> None:
    await save_session(
        phone,
        awaiting=GATE_DEPLOY,
        provider=state.get("repo_provider") or "",
        data=_gate_payload(state),
        merge_data=False,
    )


def _gate_payload(state: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "pending_task_id",
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
        "pr_review_mode",
        "review_comments",
        "review_result",
    )
    data = {k: state[k] for k in keys if state.get(k) is not None}
    data["pending_task_id"] = state.get("task_id") or data.get("pending_task_id")
    data["user_message"] = state.get("user_message") or state.get("email_body") or data.get("user_message", "")
    return data


async def hydrate_state_from_session(state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    pending = dict(session.get("data") or {})
    out = dict(state)
    for key, val in pending.items():
        if key == "pending_task_id":
            if val and not out.get("task_id"):
                out["task_id"] = val
        elif val not in (None, "", [], {}) and not out.get(key):
            out[key] = val
    if session.get("provider") and not out.get("repo_provider"):
        out["repo_provider"] = session["provider"]
    return out


async def clear_workflow_session(phone: str) -> None:
    if phone:
        await clear_session(phone)
