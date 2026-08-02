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
GATE_CI_FIX = "ci_fix_approval"
GATE_PIPELINE_WATCH = "pipeline_watching"

# Multi-turn repo / meeting picker — NOT deploy gates (must not block "1", "2", repo names).
WIZARD_PROVIDER = "provider"
WIZARD_PROJECT = "project"
WIZARD_REPO = "repo"
WIZARD_CODE = "code_instruction"
WIZARD_AWAITING = frozenset({WIZARD_PROVIDER, WIZARD_PROJECT, WIZARD_REPO, WIZARD_CODE})

WORKFLOW_GATES = frozenset(
    {
        GATE_PLAN,
        GATE_PR_MODE,
        GATE_MANUAL_PR,
        GATE_DEPLOY,
        GATE_CI_FIX,
        GATE_PIPELINE_WATCH,
        "approval",
    }
)


def multi_gate_enabled() -> bool:
    return bool(settings.enable_multi_gate_workflow)


def session_has_pr(data: dict[str, Any] | None) -> bool:
    data = data or {}
    return bool(data.get("pr_url") or data.get("pr_id") or data.get("pr_number"))


def effective_awaiting(session: dict[str, Any] | None) -> str | None:
    """Resolve the gate the user should act on.

    After PR publish, session must be pr_review_mode. If a deploy/restart left
    awaiting stuck on plan_approval but pr_url is already saved, advance the gate.
    """
    session = session or {}
    awaiting = session.get("awaiting")
    data = session.get("data") or {}
    if awaiting == GATE_PLAN and session_has_pr(data):
        logger.warning(
            "Stale gate plan_approval with PR present (task=%s) — treating as pr_review_mode",
            data.get("pending_task_id"),
        )
        return GATE_PR_MODE
    return awaiting


def is_workflow_gate(awaiting: str | None) -> bool:
    """True only for coding deploy gates — blocks parallel tasks."""
    return (awaiting or "") in WORKFLOW_GATES


def is_wizard_awaiting(awaiting: str | None) -> bool:
    return (awaiting or "") in WIZARD_AWAITING


def is_meeting_awaiting(awaiting: str | None) -> bool:
    return str(awaiting or "").startswith("meeting")


def is_conversation_awaiting(awaiting: str | None) -> bool:
    """Repo wizard or calendar slot-filling — continue same WhatsApp thread."""
    return is_wizard_awaiting(awaiting) or is_meeting_awaiting(awaiting)


def should_block_new_task(session: dict) -> bool:
    return is_workflow_gate(session.get("awaiting"))


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
    logger.info(
        "Persisted PR review-mode gate phone=%s task=%s pr=%s",
        phone,
        state.get("task_id"),
        state.get("pr_url"),
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


async def persist_pipeline_watching_gate(phone: str, state: dict[str, Any]) -> None:
    """Keep task context while Azure Pipelines runs (do not clear until STOP)."""
    await save_session(
        phone,
        awaiting=GATE_PIPELINE_WATCH,
        provider=state.get("repo_provider") or "",
        data=_gate_payload(state),
        merge_data=False,
    )


async def persist_ci_fix_gate(phone: str, state: dict[str, Any]) -> None:
    """Ask Sunny whether the test_fixer agent should repair a failed CI run."""
    await save_session(
        phone,
        awaiting=GATE_CI_FIX,
        provider=state.get("repo_provider") or "",
        data=_gate_payload(state),
        merge_data=False,
    )
    logger.info(
        "Persisted CI fix gate phone=%s task=%s build=%s",
        phone,
        state.get("task_id") or state.get("pending_task_id"),
        state.get("ci_build_id"),
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
        "ci_build_id",
        "ci_failure_summary",
        "pipeline_url",
        "pipeline_status",
        "commit_sha",
        "ci_watch_phase",
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
