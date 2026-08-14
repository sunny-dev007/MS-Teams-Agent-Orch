"""WhatsApp / Teams deploy progress — always tell Sunny where merge/deploy is."""

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


def format_merging_pr(
    task_id: str,
    pr_url: str,
    pr_id: int | str | None,
    *,
    session_id: str | None = None,
) -> str:
    from agent.services.rich_response import detect_channel, format_result_card

    ref = f"PR #{pr_id}" if pr_id else "pull request"
    return format_result_card(
        title=f"Merging (`{task_id}`)",
        ok=None,
        fields=[
            ("Action", f"Merging {ref} into `main`"),
            ("Pull request", pr_url or "N/A"),
        ],
        notes=["Next update when merge finishes or if something blocks it."],
        channel=detect_channel(session_id),
    )


def format_merge_failed(
    task_id: str,
    pr_url: str,
    reason: str,
    *,
    session_id: str | None = None,
) -> str:
    from agent.services.rich_response import detect_channel, format_result_card

    return format_result_card(
        title=f"Merge blocked (`{task_id}`)",
        ok=False,
        fields=[
            ("Pull request", pr_url or "N/A"),
            ("Issue", reason),
        ],
        metrics=[("Merge", 0.0, 10.0)],
        actions=[
            "Fix merge conflicts or policy issues in Azure DevOps",
            f"Reply *APPROVE {task_id}* to retry",
            "Reply *status* to see where things paused",
        ],
        channel=detect_channel(session_id),
    )


def format_pipeline_watching(
    task_id: str,
    pipeline_url: str,
    *,
    session_id: str | None = None,
) -> str:
    from agent.services.rich_response import detect_channel, format_result_card

    return format_result_card(
        title=f"Merged to main (`{task_id}`)",
        ok=True,
        fields=[
            ("Status", "Azure Pipeline is running now"),
            ("Pipeline", pipeline_url or "Azure Pipelines"),
        ],
        metrics=[("Merge", 10.0, 10.0), ("Deploy", 3.0, 10.0)],
        notes=[
            "I will send a *Final evaluation* when the pipeline "
            "*succeeds*, *fails*, or is *canceled*."
        ],
        actions=["Reply *status* anytime for an update"],
        channel=detect_channel(session_id),
    )


def format_deploy_step_failed(
    task_id: str,
    step: str,
    reason: str,
    *,
    session_id: str | None = None,
) -> str:
    from agent.services.rich_response import detect_channel, format_result_card

    return format_result_card(
        title=f"Deploy stopped (`{task_id}`)",
        ok=False,
        fields=[
            ("Step", step),
            ("Issue", reason),
        ],
        metrics=[("Deploy", 0.0, 10.0)],
        actions=[
            f"Reply *APPROVE {task_id}* to retry",
            "Reply *status* for pending steps",
            "Reply *stop* to clear and start fresh",
        ],
        channel=detect_channel(session_id),
    )


def format_resume_deploy_failed(task_id: str, reason: str, *, session_id: str | None = None) -> str:
    return format_deploy_step_failed(
        task_id, "Resume after approval", reason, session_id=session_id
    )
