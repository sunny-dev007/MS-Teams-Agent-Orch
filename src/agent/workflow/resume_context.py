"""Graceful WhatsApp copy when resuming or reminding Sunny where a task paused."""

from __future__ import annotations

import re
from typing import Any

from agent.workflow.gates import (
    GATE_DEPLOY,
    GATE_MANUAL_PR,
    GATE_PLAN,
    GATE_PR_MODE,
    multi_gate_enabled,
)

RESUME_STATUS_PATTERN = re.compile(
    r"(?i)^(?:"
    r"resume|continue|where\s+am\s+i|what'?s?\s+pending|pending\s+task|"
    r"task\s+status|my\s+status|status"
    r")[\s!.?]*$"
)

_GATE_LABELS = {
    GATE_PLAN: ("Gate 1 — Plan approval", "Review the implementation plan above."),
    GATE_PR_MODE: ("Gate 3 — PR review mode", "Choose how the pull request should be reviewed."),
    GATE_MANUAL_PR: ("Gate 3b — Manual PR review", "Approve the PR in GitHub or Azure DevOps."),
    GATE_DEPLOY: ("Gate 4 — Final deploy approval", "Merge to main and deploy live."),
}


def is_resume_status_message(message: str) -> bool:
    return bool(RESUME_STATUS_PATTERN.match((message or "").strip()))


def _provider_label(data: dict[str, Any]) -> str:
    p = (data.get("repo_provider") or "").lower()
    if p in ("azure_devops", "azdo", "ado"):
        return "Azure DevOps"
    if p == "github":
        return "GitHub"
    return p or "repository"


def _repo_label(data: dict[str, Any]) -> str:
    name = data.get("repo_name") or ""
    url = data.get("repo_url") or ""
    if name:
        return f"`{name}`"
    if url:
        part = url.rstrip("/").split("/")[-1]
        return f"`{part}`" if part else url
    return "selected repo"


def format_gate_hint(awaiting: str | None, data: dict[str, Any] | None) -> str:
    """Rich reminder of the current step and exact replies to continue."""
    data = data or {}
    tid = data.get("pending_task_id") or "?"
    gate_title, gate_desc = _GATE_LABELS.get(
        awaiting or "", ("Workflow paused", "Continue when ready.")
    )
    provider = _provider_label(data)
    repo = _repo_label(data)
    request = (data.get("user_message") or "")[:120]
    pr_url = data.get("pr_url") or ""

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
    else:
        lines.append("Reply *help* for the menu or *check my repos* for a new task.")

    if multi_gate_enabled() and awaiting == GATE_PLAN:
        lines.append("")
        lines.append("_Flow: Plan → Dev → PR → Review → Deploy_")

    return "\n".join(lines)


def format_resume_ack(
    gate: str,
    task_id: str,
    approval: str = "approved",
    *,
    pr_review_mode: str | None = None,
) -> str:
    """Short ack when Sunny sends the correct resume command."""
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


def format_resume_failed(task_id: str, reason: str = "") -> str:
    detail = reason or "Task checkpoint was not found on the server."
    return (
        f"*Could not resume* task `{task_id}`\n\n"
        f"{detail}\n\n"
        "*What you can do:*\n"
        "• Reply *status* to see pending steps\n"
        "• Start fresh with *check my repos*\n"
        "• If a PR was already opened, finish review in GitHub / Azure DevOps"
    )


def format_no_pending_task() -> str:
    return (
        "*No pending task* waiting for your input.\n\n"
        "*Start coding:* *check my repos* → pick GitHub or Azure DevOps\n"
        "*Or* reply *APPROVE <task_id>* if you have an id from a recent message."
    )


def has_active_gate(session: dict[str, Any]) -> bool:
    return bool(session.get("awaiting"))
