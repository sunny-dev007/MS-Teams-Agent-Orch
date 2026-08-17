from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.whatsapp import WhatsAppAuthError

logger = get_logger(__name__)

STATUS_TEMPLATES = {
    "task_started": (
        "*Sunny's AI Agent* — Task `{task_id}` started\n`[UPDATE]`\n\n"
        "── *Summary* ──\n{detail}"
    ),
    "development_complete": (
        "*Sunny's AI Agent* — Development complete (`{task_id}`)\n`[OK]`\n\n"
        "── *Changes* ──\n{detail}\n\nSending to reviewer..."
    ),
    "review_complete": (
        "*Sunny's AI Agent* — Review complete (`{task_id}`)\n`[UPDATE]`\n\n"
        "── *Result* ──\n• *Review:* {review_result}\n\n"
        "── *Comments* ──\n{detail}"
    ),
    "awaiting_plan_approval": (
        "*Sunny's AI Agent* — Implementation plan (`{task_id}`)\n`[UPDATE]`\n\n"
        "── *Summary* ──\n• *Repo:* {repo}\n\n"
        "{detail}\n\n"
        "── *Next* ──\n"
        "• Reply *PROCEED {task_id}* to start development\n"
        "• Reply *REJECT {task_id}* to cancel"
    ),
    "pr_created": (
        "*Sunny's AI Agent* — PR opened (`{task_id}`)\n`[OK]`\n\n"
        "── *Summary* ──\n• *Pull request:* {pr_url}\n\n"
        "── *Next* ──\n"
        "• *1* / *AI REVIEW* — detailed score + comments\n"
        "• *2* / *MANUAL REVIEW* — you review in GitHub / Azure DevOps"
    ),
    "awaiting_manual_pr": (
        "*Sunny's AI Agent* — Manual PR review (`{task_id}`)\n`[UPDATE]`\n\n"
        "── *Summary* ──\n• *Pull request:* {pr_url}\n\n"
        "── *Next* ──\n"
        "• Review and approve the PR in GitHub or Azure DevOps\n"
        "• Reply *PR READY {task_id}* for final deploy approval"
    ),
    "pr_review_complete": (
        "*Sunny's AI Agent* — AI PR review (`{task_id}`)\n`[UPDATE]`\n\n"
        "{detail}"
    ),
    "awaiting_approval": (
        "*Sunny's AI Agent* — Review changes before deploy (`{task_id}`)\n`[UPDATE]`\n\n"
        "── *Summary* ──\n"
        "• *Repo:* {repo}\n"
        "• *Live App Service:* nothing yet (waiting for your APPROVE)\n\n"
        "── *Proposed changes* ──\n{detail}\n\n"
        "── *Next* ──\n"
        "• Reply *APPROVE {task_id}* to merge into `main` and deploy\n"
        "• Agent-branch pipelines are validate-only until main\n"
        "• Reply *REJECT {task_id}* to cancel"
    ),
    "deploying": (
        "*Sunny's AI Agent* — Final approval received (`{task_id}`)\n`[UPDATE]`\n\n"
        "Merging to main and deploying live…"
    ),
    "pipeline_watching": (
        "*Sunny's AI Agent* — Merge complete (`{task_id}`)\n`[OK]`\n\n{detail}"
    ),
    "completed": (
        "*Sunny's AI Agent* — Deployment completed (`{task_id}`)\n`[OK]`\n\n{detail}"
    ),
    "failed": (
        "*Sunny's AI Agent* — Could not complete (`{task_id}`)\n`[FAILED]`\n\n{detail}"
    ),
    "rejected": (
        "*Sunny's AI Agent* — Rejected (`{task_id}`)\n`[FAILED]`\n\n"
        "Changes were not pushed."
    ),
    "email_summary": "*Email digest for Sunny*\n`[UPDATE]`\n\n{detail}",
    "meeting_scheduled": "*Meeting scheduled*\n`[OK]`\n\n{detail}",
    "repo_picker": "{detail}",
    "evaluation": "*Sunny's AI Agent* — Final evaluation\n`[UPDATE]`\n\n{detail}",
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

    detail = state.get("notification_text", "")
    handoff = (state.get("handoff_note") or "").strip()
    if handoff:
        detail = f"{handoff}\n\n{detail}" if detail else handoff

    text = template.format(
        task_id=state.get("task_id", "unknown"),
        detail=detail,
        review_result=state.get("review_result", ""),
        repo=state.get("repo_url", "N/A"),
        branch=state.get("branch_name", "N/A"),
        pr_url=state.get("pr_url", "N/A"),
        pipeline_url=state.get("pipeline_url", "N/A"),
    )

    try:
        from agent.services.channel_notify import send_channel_message

        await send_channel_message(phone, text)
    except WhatsAppAuthError:
        logger.error(
            "Cannot notify WhatsApp for task %s: access token expired/invalid.",
            state.get("task_id"),
        )
    except Exception:
        logger.exception(
            "Failed to send notification for task %s", state.get("task_id")
        )

    return state
