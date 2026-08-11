"""WhatsApp deploy progress — always tell Sunny where merge/deploy is."""

from __future__ import annotations

import re

from agent.core.logging import get_logger

logger = get_logger(__name__)

_RATE_RE = re.compile(r"(rate.?limit|429|timeout|timed out)", re.I)


async def send_deploy_progress(phone: str, text: str) -> None:
    if not phone or not text:
        return
    try:
        from agent.services.channel_notify import send_channel_message

        await send_channel_message(phone, text)
    except Exception:
        logger.exception("Failed deploy progress message to %s", phone)


def user_facing_deploy_error(exc: BaseException | str, *, step: str = "deployment") -> str:
    raw = str(exc)
    if _RATE_RE.search(raw):
        return (
            f"The {step} step hit a temporary API limit. "
            "Wait a minute and reply *APPROVE <task_id>* to retry."
        )
    if "401" in raw or "403" in raw or "unauthorized" in raw.lower():
        return (
            f"The {step} step was rejected by Azure DevOps (auth/permissions). "
            "Check AZDO_PAT scopes and try again."
        )
    if "not found" in raw.lower() or "404" in raw:
        return f"The {step} step could not find the PR or repository on Azure DevOps."
    if "merge" in raw.lower() and ("conflict" in raw.lower() or "not mergeable" in raw.lower()):
        return (
            f"The {step} step failed — the PR has merge conflicts. "
            "Resolve conflicts in Azure DevOps, then reply *APPROVE <task_id>* again."
        )
    return (
        f"The {step} step did not complete. "
        "Reply *status* for pending steps or *APPROVE <task_id>* to retry."
    )


def format_merging_pr(task_id: str, pr_url: str, pr_id: int | str | None) -> str:
    ref = f"PR #{pr_id}" if pr_id else "pull request"
    lines = [
        f"*Sunny's AI Agent* — Merging (`{task_id}`)",
        "",
        f"Merging {ref} into `main` now…",
    ]
    if pr_url:
        lines.append(f"*Pull request:* {pr_url}")
    lines.append("")
    lines.append("_Next update when merge finishes or if something blocks it._")
    return "\n".join(lines)


def format_merge_failed(task_id: str, pr_url: str, reason: str) -> str:
    return (
        f"*Sunny's AI Agent* — Merge blocked (`{task_id}`)\n\n"
        f"{reason}\n\n"
        f"*Pull request:* {pr_url or 'N/A'}\n\n"
        "*What to do:*\n"
        "• Fix merge conflicts or policy issues in Azure DevOps\n"
        f"• Reply *APPROVE {task_id}* to retry\n"
        "• Reply *status* to see where things paused"
    )


def format_pipeline_watching(task_id: str, pipeline_url: str) -> str:
    return (
        f"*Sunny's AI Agent* — Merged to main (`{task_id}`)\n\n"
        "Azure Pipeline is running now.\n"
        "I will send a *Final evaluation* on WhatsApp when it "
        "*succeeds*, *fails*, or is *canceled*.\n"
        f"*Pipeline:* {pipeline_url or 'Azure Pipelines'}"
    )


def format_deploy_step_failed(task_id: str, step: str, reason: str) -> str:
    return (
        f"*Sunny's AI Agent* — Deploy stopped (`{task_id}`)\n\n"
        f"*Step:* {step}\n"
        f"*Issue:* {reason}\n\n"
        "*What to do:*\n"
        f"• Reply *APPROVE {task_id}* to retry\n"
        "• Reply *status* for pending steps\n"
        "• Reply *stop* to clear and start fresh"
    )


def format_resume_deploy_failed(task_id: str, reason: str) -> str:
    return format_deploy_step_failed(task_id, "Resume after approval", reason)
