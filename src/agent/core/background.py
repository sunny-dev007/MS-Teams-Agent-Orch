import uuid

from agent.core.logging import get_logger
from agent.planner.agent import is_simple_greeting

logger = get_logger(__name__)


async def handle_whatsapp_message(parsed: dict) -> None:
    """Backward-compatible WhatsApp entry — delegates to channel handler."""
    await handle_channel_message(parsed, source="whatsapp")


async def handle_channel_message(parsed: dict, source: str = "whatsapp") -> None:
    phone = parsed["phone"]
    message = (parsed["message"] or "").strip()
    attachments = parsed.get("attachments") or []
    channel_source = source or "whatsapp"

    # Fast-path: greetings/help — always allowed.
    if is_simple_greeting(message) or message.lower() in {"help", "menu", "?", "commands"}:
        try:
            from agent.core.channel_identity import is_teams_session
            from agent.core.persona import build_greeting_reply, build_help_menu
            from agent.core.session import get_session
            from agent.services.channel_notify import send_channel_message
            from agent.workflow.gates import should_block_new_task
            from agent.workflow.resume_context import format_session_status

            session = await get_session(phone)
            channel = "teams" if is_teams_session(phone) else "whatsapp"
            is_help = message.lower() in {"help", "menu", "?", "commands"}
            if is_help:
                text = build_help_menu(channel=channel)
                if should_block_new_task(session) or session.get("awaiting"):
                    text = text + "\n\n---\n\n" + format_session_status(session)
            else:
                text = build_greeting_reply(session=session, channel=channel)
            await send_channel_message(phone, text)
            return
        except Exception:
            logger.exception("Fast-path reply failed")

    # Block only deploy gates — repo wizard replies (1, 2, repo names) must reach the graph.
    try:
        from agent.core.session import get_session
        from agent.services.channel_notify import send_channel_message
        from agent.workflow.gates import should_block_new_task
        from agent.workflow.resume_context import format_session_status, is_resume_status_message

        session = await get_session(phone)
        if should_block_new_task(session):
            await send_channel_message(phone, format_session_status(session))
            return
        if is_resume_status_message(message):
            await send_channel_message(
                phone,
                format_session_status(session)
                if session.get("awaiting")
                else "*No active task.* Reply *check my repos* to start.",
            )
            return
    except Exception:
        logger.exception("Session guard failed — continuing with graph")

    task_id = str(uuid.uuid4())[:8]
    logger.info(
        "Processing task %s from %s source=%s (msg=%s)",
        task_id,
        phone,
        channel_source,
        message[:80],
    )

    try:
        from agent.services.channel_notify import send_channel_message

        await send_channel_message(phone, "Got it, Sunny — working on it…")
    except Exception:
        logger.exception("Failed instant ack for task %s", task_id)

    try:
        from agent.agents.graph import run_graph

        await run_graph(
            task_id=task_id,
            source=channel_source,
            user_message=message,
            whatsapp_phone=phone,
            attachments=attachments or None,
        )
    except Exception as exc:
        logger.exception("Task %s failed", task_id)
        try:
            from agent.services.channel_notify import send_channel_message

            detail = str(exc)
            if "unable to open database file" in detail.lower():
                msg = (
                    f"Something went wrong with task {task_id} "
                    "(temporary storage startup). Please wait 30 seconds and try again."
                )
            else:
                msg = f"Something went wrong with task {task_id}. Please try again."
            await send_channel_message(phone, msg)
        except Exception:
            logger.exception("Failed error reply for task %s", task_id)


async def handle_gmail_notification(history_id: str) -> None:
    logger.info("Processing Gmail notification, history_id=%s", history_id)

    try:
        from agent.agents.graph import run_gmail_flow

        await run_gmail_flow(history_id)
    except Exception:
        logger.exception("Gmail flow failed for history_id=%s", history_id)
