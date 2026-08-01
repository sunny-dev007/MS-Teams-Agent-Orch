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
    "awaiting_approval": (
        "*Sunny's AI Agent* — Review changes before deploy (`{task_id}`)\n\n"
        "*Repo:* {repo}\n"
        "*Nothing pushed to main yet*\n\n"
        "*Proposed changes:*\n{detail}\n\n"
        "Reply *APPROVE {task_id}* (or *final approval*) to merge into main and deploy live.\n"
        "Reply *REJECT {task_id}* to cancel."
    ),
    "deploying": (
        "*Sunny's AI Agent* — Final approval received (`{task_id}`)\n"
        "Merging to main and deploying live…"
    ),
    "completed": (
        "*Sunny's AI Agent* — Deployment completed (`{task_id}`)\n\n{detail}"
    ),
    "failed": (
        "*Sunny's AI Agent* — Failed (`{task_id}`)\n\n*Error:* {detail}"
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
