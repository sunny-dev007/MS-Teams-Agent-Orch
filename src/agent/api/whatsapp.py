import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from agent.config import settings
from agent.core.logging import get_logger
from agent.core.security import is_phone_allowed, verify_whatsapp_signature
from agent.services.whatsapp import parse_incoming_message

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])

APPROVE_PATTERN = re.compile(r"(?i)^approve\s+(\S+)")
REJECT_PATTERN = re.compile(r"(?i)^reject\s+(\S+)")


@router.get("/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
) -> int:
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("WhatsApp webhook verified")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if settings.whatsapp_app_secret.get_secret_value() and not verify_whatsapp_signature(
        body, signature
    ):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    parsed = parse_incoming_message(payload)

    if not parsed:
        return {"status": "ok"}

    if not is_phone_allowed(parsed["phone"]):
        logger.warning("Blocked message from unauthorized phone: %s", parsed["phone"])
        return {"status": "ok"}

    message = parsed["message"].strip()
    logger.info("Received message from %s: %s", parsed["phone"], message[:100])

    approve_match = APPROVE_PATTERN.match(message)
    reject_match = REJECT_PATTERN.match(message)

    if approve_match:
        task_id = approve_match.group(1)
        logger.info("Approval received for task %s", task_id)
        background_tasks.add_task(
            _resume_with_approval, task_id, "approved", parsed["phone"]
        )
    elif reject_match:
        task_id = reject_match.group(1)
        logger.info("Rejection received for task %s", task_id)
        background_tasks.add_task(
            _resume_with_approval, task_id, "rejected", parsed["phone"]
        )
    else:
        from agent.core.background import handle_whatsapp_message
        background_tasks.add_task(handle_whatsapp_message, parsed)

    return {"status": "ok"}


async def _resume_with_approval(task_id: str, approval: str, phone: str) -> None:
    try:
        from agent.agents.graph import resume_graph
        await resume_graph(task_id, approval, phone)
    except Exception:
        logger.exception("Failed to resume task %s with approval=%s", task_id, approval)
        from agent.services.whatsapp import send_message
        await send_message(phone, f"Failed to process approval for task {task_id}.")
