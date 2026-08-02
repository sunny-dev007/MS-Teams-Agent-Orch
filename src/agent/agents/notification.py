from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.whatsapp import WhatsAppAuthError, send_message

logger = get_logger(__name__)

STATUS_TEMPLATES = {
    "task_started": (
        "*Sunny's AI Agent* — Task `{task_id}` started\n\n{detail}"
    ),
    "development_complete": (
        "*Sunny's AI Agent* — Development complete (`{task_id}`)\n\n"
        "*Changes:*\n{detail}\n\nSending to reviewer..."
    ),
    "review_complete": (
        "*Sunny's AI Agent* — Review complete (`{task_id}`)\n\n"
        "*Result:* {review_result}\n"
        "*Comments:*\n{detail}"
    ),
    "awaiting_plan_approval": (
        "*Sunny's AI Agent* — Implementation plan (`{task_id}`)\n\n"
        "*Repo:* {repo}\n\n"
        "{detail}\n\n"
        "Reply *PROCEED {task_id}* to start development.\n"
        "Reply *REJECT {task_id}* to cancel."
    ),
    "pr_created": (
        "*Sunny's AI Agent* — PR opened (`{task_id}`)\n\n"
        "*Pull request:* {pr_url}\n\n"
        "How should this PR be reviewed?\n"
        "1. *AI review* — detailed score + comments on WhatsApp\n"
        "2. *Manual review* — you review in GitHub / Azure DevOps\n\n"
        "Reply *1* or *AI REVIEW* / *2* or *MANUAL REVIEW*"
    ),
    "awaiting_manual_pr": (
        "*Sunny's AI Agent* — Manual PR review (`{task_id}`)\n\n"
        "*Pull request:* {pr_url}\n\n"
        "Review and approve the PR in GitHub or Azure DevOps.\n"
        "When done, reply *PR READY {task_id}* for final deploy approval."
    ),
    "pr_review_complete": (
        "*Sunny's AI Agent* — AI PR review (`{task_id}`)\n\n"
        "{detail}"
    ),
    "awaiting_approval": (
        "*Sunny's AI Agent* — Review changes before deploy (`{task_id}`)\n\n"
        "*Repo:* {repo}\n"
        "*Nothing on live App Service yet*\n\n"
        "*Proposed changes:*\n{detail}\n\n"
        "Reply *APPROVE {task_id}* (or *final approval*) to *merge into main* "
        "and run the real Deploy stage on App Service.\n"
        "(Pipeline runs on `agent/*` are validate-only — Deploy stays skipped until main.)\n"
        "Reply *REJECT {task_id}* to cancel."
    ),
    "deploying": (
        "*Sunny's AI Agent* — Final approval received (`{task_id}`)\n"
        "Merging to main and deploying live…"
    ),
    "pipeline_watching": (
        "*Sunny's AI Agent* — Merge complete (`{task_id}`)\n\n{detail}"
    ),
    "completed": (
        "*Sunny's AI Agent* — Deployment completed (`{task_id}`)\n\n{detail}"
    ),
    "failed": (
        "*Sunny's AI Agent* — Could not complete (`{task_id}`)\n\n{detail}"
    ),
    "rejected": (
        "*Sunny's AI Agent* — Rejected (`{task_id}`). Changes were not pushed."
    ),
    "email_summary": "*Email digest for Sunny*\n\n{detail}",
    "meeting_scheduled": "*Meeting scheduled*\n\n{detail}",
    "repo_picker": "{detail}",
    "evaluation": "*Final evaluation*\n\n{detail}",
    "general_response": "{detail}",
    "ack": "{detail}",
}


async def notify(state: AgentState) -> AgentState:
    phone = state.get("whatsapp_phone", "")
    if not phone:
        logger.warning("No phone number for notification, task %s", state.get("task_id"))
        return state

    status = state.get("status", "general_response")
    if status == "pipeline_watching":
        # deploy_notify already sent merge + pipeline-watching progress.
        return state

    template = STATUS_TEMPLATES.get(status, STATUS_TEMPLATES["general_response"])

    text = template.format(
        task_id=state.get("task_id", "unknown"),
        detail=state.get("notification_text", ""),
        review_result=state.get("review_result", ""),
        repo=state.get("repo_url", "N/A"),
        branch=state.get("branch_name", "N/A"),
        pr_url=state.get("pr_url", "N/A"),
        pipeline_url=state.get("pipeline_url", "N/A"),
    )

    try:
        await send_message(phone, text)
    except WhatsAppAuthError:
        logger.error(
            "Cannot notify WhatsApp for task %s: access token expired/invalid.",
            state.get("task_id"),
        )
    except Exception:
        logger.exception(
            "Failed to send WhatsApp notification for task %s", state.get("task_id")
        )

    return state
