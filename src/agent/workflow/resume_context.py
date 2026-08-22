"""Graceful WhatsApp copy when resuming or reminding Sunny where a task paused."""

from __future__ import annotations

import re
from typing import Any

from agent.workflow.gates import (
    GATE_CI_FIX,
    GATE_DEPLOY,
    GATE_MANUAL_PR,
    GATE_PIPELINE_WATCH,
    GATE_PLAN,
    GATE_PR_MODE,
    WIZARD_CODE,
    WIZARD_PROJECT,
    WIZARD_PROVIDER,
    WIZARD_REPO,
    is_conversation_awaiting,
    is_workflow_gate,
    is_wizard_awaiting,
    multi_gate_enabled,
)

RESUME_STATUS_PATTERN = re.compile(
    r"(?i)^(?:"
    r"resume|continue|where\s+am\s+i|what'?s?\s+pending|pending\s+task|"
    r"task\s+status|my\s+status|status"
    r")[\s!.?]*$"
)

CANCEL_SESSION_PATTERN = re.compile(
    r"(?i)^(?:"
    r"stop(?:\s+(?:my\s+)?(?:previous\s+)?task)?|cancel(?:\s+task)?|abort|"
    r"reset(?:\s+session)?|start\s+over|forget(?:\s+(?:that|previous|last))?|"
    r"(?:i\s+)?(?:want\s+)?new\s+task|clear(?:\s+session)?"
    r")[\s!.?]*$"
)

RESTART_SESSION_PATTERN = re.compile(
    r"(?i)^(?:"
    r"check\s+(?:my\s+)?repos?|browse\s+repos?|repositories|"
    r"(?:start\s+)?new\s+task|start\s+over"
    r")[\s!.?]*$"
)

_GATE_LABELS = {
    GATE_PLAN: ("Gate 1 — Plan approval", "Review the implementation plan above."),
    GATE_PR_MODE: ("Gate 3 — PR review mode", "Choose how the pull request should be reviewed."),
    GATE_MANUAL_PR: ("Gate 3b — Manual PR review", "Approve the PR in GitHub or Azure DevOps."),
    GATE_DEPLOY: ("Gate 4 — Final deploy approval", "Merge to main and deploy live."),
    GATE_PIPELINE_WATCH: (
        "CI running",
        "Azure Pipelines is still running. I'll message you when tests finish.",
    ),
    GATE_CI_FIX: (
        "CI / tests failed",
        "Reply FIX TESTS to let the test_fixer agent repair, or SKIP / STOP.",
    ),
}

_WIZARD_LABELS = {
    WIZARD_PROVIDER: (
        "Choose provider",
        "Reply *1* for GitHub or *2* for Azure DevOps.",
    ),
    WIZARD_PROJECT: (
        "Choose Azure DevOps project",
        "Reply with the project *number* or exact name.",
    ),
    WIZARD_REPO: (
        "Choose repository",
        "Reply with the repo *number* or exact name.",
    ),
    WIZARD_CODE: (
        "Describe your change",
        "Tell me what to build or fix in the selected repo.",
    ),
}


def is_resume_status_message(message: str) -> bool:
    return bool(RESUME_STATUS_PATTERN.match((message or "").strip()))


def is_cancel_session_message(message: str) -> bool:
    return bool(CANCEL_SESSION_PATTERN.match((message or "").strip()))


def is_restart_session_message(message: str) -> bool:
    return bool(RESTART_SESSION_PATTERN.match((message or "").strip()))


def _safe_session_text(value: Any, *, limit: int = 0) -> str:
    """Coerce session field values to display text (gate data may be non-string)."""
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        try:
            import json

            text = json.dumps(value, default=str)
        except Exception:
            text = str(value)
    text = text.strip()
    if limit and len(text) > limit:
        return text[:limit]
    return text


def _provider_label(data: dict[str, Any], session_provider: str = "") -> str:
    p = (data.get("repo_provider") or session_provider or "").lower()
    if p in ("azure_devops", "azdo", "ado"):
        return "Azure DevOps"
    if p == "github":
        return "GitHub"
    return p or "not selected"


def _repo_label(data: dict[str, Any]) -> str:
    name = data.get("repo_name") or ""
    url = data.get("repo_url") or ""
    if name:
        return f"`{name}`"
    if url:
        part = url.rstrip("/").split("/")[-1]
        return f"`{part}`" if part else url
    return "not selected yet"


def format_wizard_hint(awaiting: str | None, data: dict[str, Any] | None, session_provider: str = "") -> str:
    data = data or {}
    title, desc = _WIZARD_LABELS.get(
        awaiting or "", ("Continue setup", "Reply to continue.")
    )
    provider = _provider_label(data, session_provider)
    lines = [
        "*Sunny's AI Agent* — Continue setup",
        "",
        f"*Step:* {title}",
        f"*Provider:* {provider}",
    ]
    if data.get("azdo_project"):
        lines.append(f"*Project:* `{data['azdo_project']}`")
    if data.get("repo_name"):
        lines.append(f"*Repo:* `{data['repo_name']}`")
    lines.extend(["", desc, "", "_Reply *stop* or *new task* to cancel and start fresh._"])
    return "\n".join(lines)


def format_gate_hint(
    awaiting: str | None,
    data: dict[str, Any] | None,
    session_provider: str = "",
) -> str:
    """Rich reminder for deploy gates OR wizard steps."""
    if is_wizard_awaiting(awaiting) or str(awaiting or "").startswith("meeting"):
        return format_wizard_hint(awaiting, data, session_provider)

    data = data or {}
    tid = data.get("pending_task_id") or "?"
    gate_title, gate_desc = _GATE_LABELS.get(
        awaiting or "", ("Workflow paused", "Continue when ready.")
    )
    provider = _provider_label(data, session_provider)
    repo = _repo_label(data)
    request = _safe_session_text(data.get("user_message"), limit=120)
    pr_url = _safe_session_text(data.get("pr_url"))

    lines = [
        f"*Sunny's AI Agent* — Resume task `{tid}`",
        "",
        f"*Step:* {gate_title}",
        f"*Provider:* {provider} · *Repo:* {repo}",
    ]
    if request:
        lines.append(f"*Request:* _{request}_")
    lines.append("")
    lines.append(gate_desc)
    lines.append("")

    if awaiting == GATE_PLAN:
        lines.extend([
            "*Your options:*",
            f"• *PROCEED {tid}* — start development on this plan",
            f"• *REJECT {tid}* — cancel (no code changes)",
            "",
            "_Tip: *go ahead* or *approve plan* also works._",
        ])
    elif awaiting == GATE_PR_MODE:
        lines.extend([
            "*Your options:*",
            "• *1* or *AI REVIEW* — detailed score + review on WhatsApp",
            f"• *2* or *MANUAL REVIEW* — review in {provider}",
            "",
            "_Live deploy runs only after *APPROVE* merges to `main`. "
            "Agent-branch pipelines are validate-only (Deploy skipped)._",
        ])
        if pr_url:
            lines.append(f"\n*PR:* {pr_url}")
    elif awaiting == GATE_MANUAL_PR:
        lines.extend([
            f"*Pull request:* {pr_url or 'open GitHub / Azure DevOps'}",
            "",
            f"1. Review and approve in {provider}",
            f"2. Reply *PR READY {tid}* here for final deploy approval",
        ])
    elif awaiting in (GATE_DEPLOY, "approval"):
        lines.extend([
            "*Nothing merged to main yet.*",
            "",
            f"• *APPROVE {tid}* — merge + deploy live",
            f"• *REJECT {tid}* — cancel deploy",
            "",
            "_You will get a Final evaluation when the pipeline finishes._",
        ])
    elif awaiting == GATE_PIPELINE_WATCH:
        lines.extend([
            "Azure Pipelines is still running for this task.",
            "I'll message you when *Run tests* finishes.",
            "",
            "_Your conversation context is kept until you reply *STOP* or *check my repos*._",
        ])
        if data.get("pipeline_url"):
            lines.append(f"\n*Pipeline:* {data.get('pipeline_url')}")
    elif awaiting == GATE_CI_FIX:
        lines.extend([
            "Build / tests failed. The *test_fixer* agent can repair them.",
            "",
            f"• *FIX TESTS {tid}* — AI identifies failing tests and pushes a fix",
            f"• *SKIP {tid}* — leave the failure as-is (keeps context)",
            "• *STOP* — clear the whole session",
        ])
        if data.get("pipeline_url"):
            lines.append(f"\n*Pipeline:* {data.get('pipeline_url')}")
    else:
        lines.append("Reply *help* for the menu or *check my repos* for a new task.")

    lines.append("")
    lines.append("_Reply *stop* or *new task* to cancel this workflow._")

    if multi_gate_enabled() and awaiting == GATE_PLAN:
        lines.append("_Flow: Plan → Dev → PR → Review → Deploy_")

    return "\n".join(lines)


def format_session_status(session: dict[str, Any]) -> str:
    awaiting = session.get("awaiting")
    data = session.get("data") or {}
    if not awaiting:
        return format_no_pending_task()
    return format_gate_hint(awaiting, data, session.get("provider") or "")


def format_session_cleared(*, had_task_id: str = "", had_gate: str = "") -> str:
    lines = ["*Session cleared.*", ""]
    if had_task_id and is_workflow_gate(had_gate):
        lines.append(
            f"Abandoned coding task `{had_task_id}` (nothing new was merged)."
        )
        lines.append("")
    lines.extend([
        "You can start fresh:",
        "• *check my repos* — browse GitHub / Azure DevOps",
        "• *list my documents* — Knowledge (SP / OD / ON)",
        "• *help* — full tools & prompts catalog",
    ])
    return "\n".join(lines)


def format_resume_ack(
    gate: str,
    task_id: str,
    approval: str = "approved",
    *,
    pr_review_mode: str | None = None,
) -> str:
    if gate == GATE_PLAN and approval == "rejected":
        return format_plan_rejected(task_id)
    if gate == GATE_PLAN:
        return (
            f"*Plan approved* (`{task_id}`)\n\n"
            "Starting development on your approved scope.\n"
            "I'll message you when coding is complete and the PR is ready."
        )
    if gate == GATE_PR_MODE and pr_review_mode == "ai":
        return (
            f"*AI PR review* (`{task_id}`)\n\n"
            "Analyzing the pull request.\n"
            "You'll get a scored review, then a final *APPROVE* prompt."
        )
    if gate == GATE_PR_MODE and pr_review_mode == "manual":
        return (
            f"*Manual review selected* (`{task_id}`)\n\n"
            "Review the PR in GitHub or Azure DevOps.\n"
            f"Reply *PR READY {task_id}* when you've approved it there."
        )
    if gate == GATE_MANUAL_PR:
        return (
            f"*PR ready* (`{task_id}`)\n\n"
            "Preparing final deploy approval on WhatsApp.\n"
            "You'll be asked to *APPROVE* before anything merges to main."
        )
    return f"Continuing task `{task_id}`…"


def format_plan_rejected(task_id: str) -> str:
    return (
        f"*Plan rejected* (`{task_id}`)\n\n"
        "No code was written and nothing was pushed.\n"
        "Send a new request or *check my repos* to start again."
    )


def format_no_pending_task() -> str:
    return (
        "*No pending task* waiting for your input.\n\n"
        "*Start coding:* *check my repos* → pick GitHub or Azure DevOps\n"
        "*Plan waiting:* reply *PROCEED <task_id>*\n"
        "*Deploy waiting:* reply *APPROVE <task_id>*"
    )


def format_resume_failed(task_id: str, reason: str = "") -> str:
    detail = reason or (
        "Task checkpoint was not found on the server "
        "(often after an app restart). Start the change again."
    )
    return (
        f"*Could not resume* task `{task_id}`\n\n"
        f"{detail}\n\n"
        "*What you can do:*\n"
        "• *stop* to clear session\n"
        "• *check my repos* to start fresh"
    )


def has_active_gate(session: dict[str, Any]) -> bool:
    """Deploy gate only — repo wizard steps are NOT gates."""
    return is_workflow_gate(session.get("awaiting"))


def should_continue_conversation(session: dict[str, Any]) -> bool:
    return is_conversation_awaiting(session.get("awaiting"))
