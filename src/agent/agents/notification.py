from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.whatsapp import send_message

logger = get_logger(__name__)

STATUS_TEMPLATES = {
    "task_started": "🤖 *Task {task_id}* started.\n\n{detail}",
    "development_complete": (
        "🔧 *Task {task_id}* — Development complete.\n\n"
        "*Changes:*\n{detail}\n\n"
        "Sending to reviewer..."
    ),
    "review_complete": (
        "📝 *Task {task_id}* — Review complete.\n\n"
        "*Result:* {review_result}\n"
        "*Comments:*\n{detail}"
    ),
    "awaiting_approval": (
        "✅ *Task {task_id}* — Ready for your approval!\n\n"
        "*Repo:* {repo}\n"
        "*Branch:* {branch}\n"
        "*Changes:*\n{detail}\n\n"
        "Reply *APPROVE {task_id}* or *REJECT {task_id}*"
    ),
    "deploying": "🚀 *Task {task_id}* — Pushing code and triggering pipeline...",
    "completed": (
        "✅ *Task {task_id}* — Completed!\n\n"
        "*PR:* {pr_url}\n"
        "*Pipeline:* {pipeline_url}"
    ),
    "failed": "❌ *Task {task_id}* — Failed.\n\n*Error:* {detail}",
    "rejected": "🚫 *Task {task_id}* — Rejected. Changes discarded.",
    "email_summary": "📧 *Email Summary:*\n\n{detail}",
    "general_response": "{detail}",
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
    except Exception:
        logger.exception("Failed to send WhatsApp notification for task %s", state.get("task_id"))

    return state
