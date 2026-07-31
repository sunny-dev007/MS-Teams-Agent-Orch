import uuid

from agent.core.logging import get_logger

logger = get_logger(__name__)


async def handle_whatsapp_message(parsed: dict) -> None:
    task_id = str(uuid.uuid4())[:8]
    logger.info("Processing task %s from %s", task_id, parsed["phone"])

    try:
        from agent.agents.graph import run_graph

        await run_graph(
            task_id=task_id,
            source="whatsapp",
            user_message=parsed["message"],
            whatsapp_phone=parsed["phone"],
        )
    except Exception:
        logger.exception("Task %s failed", task_id)
        from agent.services.whatsapp import send_message

        await send_message(parsed["phone"], f"Something went wrong with task {task_id}.")


async def handle_gmail_notification(history_id: str) -> None:
    logger.info("Processing Gmail notification, history_id=%s", history_id)

    try:
        from agent.agents.graph import run_gmail_flow

        await run_gmail_flow(history_id)
    except Exception:
        logger.exception("Gmail flow failed for history_id=%s", history_id)
