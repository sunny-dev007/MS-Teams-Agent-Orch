import uuid

from agent.core.logging import get_logger

logger = get_logger(__name__)


async def handle_whatsapp_message(parsed: dict) -> None:
    task_id = str(uuid.uuid4())[:8]
    phone = parsed["phone"]
    message = parsed["message"]
    logger.info("Processing task %s from %s", task_id, phone)

    # Instant ack so Sunny sees feedback within seconds (before LLM / graph work).
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
