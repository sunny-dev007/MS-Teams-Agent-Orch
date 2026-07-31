import base64
import json

from fastapi import APIRouter, BackgroundTasks, Request

from agent.core.background import handle_gmail_notification
from agent.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["gmail"])


@router.post("/gmail")
async def gmail_push(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()

    try:
        pubsub_message = body.get("message", {})
        data = pubsub_message.get("data", "")
        decoded = json.loads(base64.b64decode(data).decode("utf-8"))
        history_id = str(decoded.get("historyId", ""))
    except Exception:
        logger.warning("Failed to parse Gmail push notification")
        return {"status": "ok"}

    if history_id:
        logger.info("Gmail push notification received, historyId=%s", history_id)
        background_tasks.add_task(handle_gmail_notification, history_id)

    return {"status": "ok"}
