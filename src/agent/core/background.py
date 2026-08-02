import uuid

from agent.core.logging import get_logger
from agent.planner.agent import is_simple_greeting

logger = get_logger(__name__)


import uuid

from agent.core.logging import get_logger
from agent.planner.agent import is_simple_greeting

logger = get_logger(__name__)


async def handle_whatsapp_message(parsed: dict) -> None:
    phone = parsed["phone"]
    message = (parsed["message"] or "").strip()

    # Fast-path: greetings/help — always allowed even during a paused workflow.
    if is_simple_greeting(message) or message.lower() in {"help", "menu", "?", "commands"}:
        try:
            from agent.core.persona import GREETING_REPLY, HELP_MENU
            from agent.core.session import get_session
            from agent.services.whatsapp import send_message
            from agent.workflow.resume_context import format_gate_hint, has_active_gate

            session = await get_session(phone)
            if has_active_gate(session) and message.lower() in {"help", "menu", "?", "commands"}:
                text = HELP_MENU + "\n\n---\n\n" + format_gate_hint(
                    session.get("awaiting"), session.get("data")
                )
            else:
                text = (
                    HELP_MENU
                    if message.lower() in {"help", "menu", "?", "commands"}
                    else GREETING_REPLY
                )
            await send_message(phone, text)
            return
        except Exception:
            logger.exception("Fast-path reply failed")
            # Fall through

    # Do not start a brand-new task while a gate is waiting — guide Sunny to resume.
    try:
        from agent.core.session import get_session
        from agent.services.whatsapp import send_message
        from agent.workflow.resume_context import format_gate_hint, has_active_gate, is_resume_status_message

        session = await get_session(phone)
        if has_active_gate(session):
            await send_message(
                phone,
                format_gate_hint(session.get("awaiting"), session.get("data")),
            )
            return
        if is_resume_status_message(message):
            await send_message(phone, "*No active task.* Reply *check my repos* to start.")
            return
    except Exception:
        logger.exception("Active-gate check failed — continuing with new task")

    task_id = str(uuid.uuid4())[:8]
    logger.info("Processing task %s from %s", task_id, phone)
    try:
        from agent.services.whatsapp import send_message

        await send_message(
            phone,
            "Got it, Sunny — working on it…",
        )
    except Exception:
        logger.exception("Failed to send instant ack for task %s", task_id)

    try:
        from agent.agents.graph import run_graph

        await run_graph(
            task_id=task_id,
            source="whatsapp",
            user_message=message,
            whatsapp_phone=phone,
        )
    except Exception:
        logger.exception("Task %s failed", task_id)
        try:
            from agent.services.whatsapp import send_message

            await send_message(
                phone,
                f"Something went wrong with task {task_id}. Please try again.",
            )
        except Exception:
            logger.exception(
                "Also failed to send error reply for task %s to %s",
                task_id,
                phone,
            )


async def handle_gmail_notification(history_id: str) -> None:
    logger.info("Processing Gmail notification, history_id=%s", history_id)

    try:
        from agent.agents.graph import run_gmail_flow

        await run_gmail_flow(history_id)
    except Exception:
        logger.exception("Gmail flow failed for history_id=%s", history_id)
