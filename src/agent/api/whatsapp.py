import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from agent.config import settings
from agent.core.logging import get_logger
from agent.core.security import is_phone_allowed, verify_whatsapp_signature
from agent.services.whatsapp import parse_incoming_message

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])

# Final deployment approval phrases (optional task id)
# Examples: Approve | APPROVE abc123 | final approval | approve deploy | deploy now
APPROVE_PATTERN = re.compile(
    r"(?i)^(?:"
    r"approve(?:\s+deploy(?:ment)?)?(?:\s+([A-Za-z0-9_-]+))?"
    r"|final\s+approval(?:\s+(?:for\s+)?deploy(?:ment)?)?(?:\s+([A-Za-z0-9_-]+))?"
    r"|deploy(?:\s+now)?(?:\s+([A-Za-z0-9_-]+))?"
    r")[\s!.]*$"
)
REJECT_PATTERN = re.compile(r"(?i)^reject(?:\s+([A-Za-z0-9_-]+))?[\s!.]*$")


def _task_id_from_match(match: re.Match | None) -> str | None:
    if not match:
        return None
    for group in match.groups():
        if group:
            return group
    return None


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
        background_tasks.add_task(
            _resume_with_approval,
            _task_id_from_match(approve_match),
            "approved",
            parsed["phone"],
        )
    elif reject_match:
        background_tasks.add_task(
            _resume_with_approval,
            _task_id_from_match(reject_match),
            "rejected",
            parsed["phone"],
        )
    else:
        from agent.core.background import handle_whatsapp_message
        background_tasks.add_task(handle_whatsapp_message, parsed)

    return {"status": "ok"}


async def _resolve_task_id(phone: str, explicit_task_id: str | None) -> str | None:
    if explicit_task_id:
        return explicit_task_id.strip()
    from agent.core.session import get_session

    session = await get_session(phone)
    if session.get("awaiting") == "approval":
        tid = (session.get("data") or {}).get("pending_task_id")
        if tid:
            return str(tid)
    return None


async def _resume_with_approval(
    explicit_task_id: str | None, approval: str, phone: str
) -> None:
    from agent.services.whatsapp import send_message

    task_id = await _resolve_task_id(phone, explicit_task_id)
    if not task_id:
        try:
            await send_message(
                phone,
                "I don't have a pending coding task to approve.\n\n"
                "Reply *APPROVE <task_id>* using the id from the approval message "
                "(e.g. `APPROVE a71b354f`), or start a new change with *check my repos*.",
            )
        except Exception:
            logger.exception("Failed to send missing-approval reply")
        return

    try:
        await send_message(
            phone,
            f"Got it, Sunny — final approval received for `{task_id}`.\n"
            "Merging to main and deploying live…",
        )
    except Exception:
        logger.exception("Failed to send approval ack for task %s", task_id)

    try:
        from agent.agents.graph import resume_graph

        await resume_graph(task_id, approval, phone)
    except Exception:
        logger.exception("Failed to resume task %s with approval=%s", task_id, approval)
        await send_message(phone, f"Failed to process approval for task {task_id}.")
