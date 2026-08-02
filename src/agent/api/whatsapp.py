import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from agent.config import settings
from agent.core.logging import get_logger
from agent.core.security import is_phone_allowed, verify_whatsapp_signature
from agent.services.whatsapp import parse_incoming_message

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])

# Final deployment approval phrases (optional task id)
PROCEED_PATTERN = re.compile(
    r"(?i)^(?:"
    r"proceed|approve\s+plan|start\s+(?:dev|development)|go\s+ahead(?:\s+with\s+plan)?"
    r")(?:\s+([A-Za-z0-9_-]+))?[\s!.]*$"
)
PR_AI_PATTERN = re.compile(
    r"(?i)^(?:1|ai(?:\s+review)?|review\s+with\s+ai)(?:\s+([A-Za-z0-9_-]+))?[\s!.]*$"
)
PR_MANUAL_PATTERN = re.compile(
    r"(?i)^(?:2|manual(?:\s+review)?|review\s+manually)(?:\s+([A-Za-z0-9_-]+))?[\s!.]*$"
)
PR_READY_PATTERN = re.compile(
    r"(?i)^(?:"
    r"pr\s+(?:ready|approved|done)|manual\s+(?:approved|done)"
    r")(?:\s+([A-Za-z0-9_-]+))?[\s!.]*$"
)
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

    from agent.core.session import get_session
    from agent.workflow.gates import (
        GATE_DEPLOY,
        GATE_MANUAL_PR,
        GATE_PLAN,
        GATE_PR_MODE,
        is_conversation_awaiting,
        is_workflow_gate,
    )
    from agent.workflow.resume_context import (
        format_gate_hint,
        format_no_pending_task,
        format_session_cleared,
        format_session_status,
        is_cancel_session_message,
        is_restart_session_message,
        is_resume_status_message,
    )

    session = await get_session(parsed["phone"])
    awaiting = session.get("awaiting")
    session_data = dict(session.get("data") or {})
    session_provider = session.get("provider") or ""

    # Cancel / fresh start — always honored.
    if is_cancel_session_message(message):
        tid = session_data.get("pending_task_id") or ""
        background_tasks.add_task(_clear_and_notify, parsed["phone"], tid, awaiting or "")
        return {"status": "ok"}

    if is_restart_session_message(message):
        tid = session_data.get("pending_task_id") or ""
        background_tasks.add_task(_clear_and_restart, parsed["phone"], message, tid, awaiting or "")
        return {"status": "ok"}

    if is_resume_status_message(message):
        background_tasks.add_task(
            _send_gate_hint,
            parsed["phone"],
            format_session_status(session) if session.get("awaiting") else format_no_pending_task(),
        )
        return {"status": "ok"}

    proceed_match = PROCEED_PATTERN.match(message)
    reject_match = REJECT_PATTERN.match(message)
    approve_match = APPROVE_PATTERN.match(message)
    pr_ai_match = PR_AI_PATTERN.match(message)
    pr_manual_match = PR_MANUAL_PATTERN.match(message)
    pr_ready_match = PR_READY_PATTERN.match(message)

    if awaiting == GATE_PLAN:
        if reject_match:
            background_tasks.add_task(
                _resume_gate, GATE_PLAN, "rejected", parsed["phone"], _task_id_from_match(reject_match)
            )
        elif proceed_match or (
            approve_match
            and (message.lower().strip() in ("approve", "approved") or "plan" in message.lower())
        ):
            tid = _task_id_from_match(proceed_match) or _task_id_from_match(approve_match)
            background_tasks.add_task(
                _resume_gate, GATE_PLAN, "approved", parsed["phone"], tid
            )
        else:
            background_tasks.add_task(
                _send_gate_hint,
                parsed["phone"],
                format_gate_hint(GATE_PLAN, session_data, session_provider),
            )
    elif awaiting == GATE_PR_MODE:
        if pr_ai_match:
            background_tasks.add_task(
                _resume_gate,
                GATE_PR_MODE,
                "approved",
                parsed["phone"],
                _task_id_from_match(pr_ai_match),
                pr_review_mode="ai",
            )
        elif pr_manual_match:
            background_tasks.add_task(
                _resume_gate,
                GATE_PR_MODE,
                "approved",
                parsed["phone"],
                _task_id_from_match(pr_manual_match),
                pr_review_mode="manual",
            )
        else:
            background_tasks.add_task(
                _send_gate_hint,
                parsed["phone"],
                format_gate_hint(GATE_PR_MODE, session_data, session_provider),
            )
    elif awaiting == GATE_MANUAL_PR:
        if pr_ready_match:
            background_tasks.add_task(
                _resume_gate,
                GATE_MANUAL_PR,
                "approved",
                parsed["phone"],
                _task_id_from_match(pr_ready_match),
            )
        else:
            background_tasks.add_task(
                _send_gate_hint,
                parsed["phone"],
                format_gate_hint(GATE_MANUAL_PR, session_data, session_provider),
            )
    elif is_conversation_awaiting(awaiting):
        # Repo wizard / meeting — continue multi-turn thread (e.g. "1" = GitHub).
        from agent.core.background import handle_whatsapp_message
        background_tasks.add_task(handle_whatsapp_message, parsed)
    elif approve_match and awaiting in (GATE_DEPLOY, "approval", None):
        background_tasks.add_task(
            _resume_with_approval,
            _task_id_from_match(approve_match),
            "approved",
            parsed["phone"],
        )
    elif reject_match and awaiting in (GATE_DEPLOY, "approval", None):
        background_tasks.add_task(
            _resume_with_approval,
            _task_id_from_match(reject_match),
            "rejected",
            parsed["phone"],
        )
    elif is_workflow_gate(awaiting):
        background_tasks.add_task(
            _send_gate_hint,
            parsed["phone"],
            format_gate_hint(awaiting, session_data, session_provider),
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
    if session.get("awaiting"):
        tid = (session.get("data") or {}).get("pending_task_id")
        if tid:
            return str(tid)
    return None


async def _pending_deploy_context(phone: str, task_id: str) -> dict[str, str]:
    """Load provider-isolated repo context for CI gate (session first)."""
    from agent.core.session import get_session
    from agent.services.ci_gate import context_from_session_data

    session = await get_session(phone)
    data = dict(session.get("data") or {})
    if session.get("provider") and not data.get("repo_provider"):
        data["repo_provider"] = session.get("provider")
    ctx = context_from_session_data(data)
    if ctx.get("provider") and (ctx.get("repo_name") or ctx.get("project")):
        return ctx

    # Fallback: hydrate from LangGraph checkpoint if session was thin
    try:
        from agent.agents import graph as graph_mod

        compiled = await graph_mod._get_compiled()
        snap = await compiled.aget_state({"configurable": {"thread_id": task_id}})
        values = dict(snap.values or {})
        return context_from_session_data(
            {
                "repo_provider": values.get("repo_provider") or "",
                "repo_owner": values.get("repo_owner") or "",
                "repo_name": values.get("repo_name") or "",
                "repo_url": values.get("repo_url") or "",
                "azdo_project": values.get("azdo_project") or "",
                "azdo_repo_id": values.get("azdo_repo_id") or "",
            }
        )
    except Exception:
        logger.warning("Could not load checkpoint context for task %s", task_id)
        return ctx


async def _clear_and_notify(phone: str, task_id: str, awaiting: str) -> None:
    from agent.core.session import clear_session
    from agent.services.whatsapp import send_message
    from agent.workflow.resume_context import format_session_cleared

    await clear_session(phone)
    try:
        await send_message(phone, format_session_cleared(had_task_id=task_id, had_gate=awaiting))
    except Exception:
        logger.exception("Failed cancel notify for %s", phone)


async def _clear_and_restart(phone: str, message: str, task_id: str, awaiting: str) -> None:
    from agent.core.session import clear_session
    from agent.services.whatsapp import send_message
    from agent.workflow.resume_context import format_session_cleared

    await clear_session(phone)
    try:
        if task_id or awaiting:
            await send_message(
                phone,
                format_session_cleared(had_task_id=task_id, had_gate=awaiting) + "\n\n_Starting fresh…_",
            )
    except Exception:
        logger.exception("Failed restart notify for %s", phone)

    from agent.core.background import handle_whatsapp_message

    await handle_whatsapp_message({"phone": phone, "message": message})


async def _send_gate_hint(phone: str, text: str) -> None:
    from agent.services.whatsapp import send_message

    try:
        await send_message(phone, text)
    except Exception:
        logger.exception("Failed to send gate hint to %s", phone)


async def _resume_gate(
    gate: str,
    approval: str,
    phone: str,
    explicit_task_id: str | None,
    *,
    pr_review_mode: str | None = None,
) -> None:
    from agent.services.whatsapp import send_message
    from agent.workflow.gates import GATE_MANUAL_PR, GATE_PLAN, GATE_PR_MODE, clear_workflow_session
    from agent.workflow.resume_context import format_resume_ack, format_resume_failed

    task_id = await _resolve_task_id(phone, explicit_task_id)
    if not task_id:
        from agent.workflow.resume_context import format_no_pending_task

        await send_message(phone, format_no_pending_task())
        return

    try:
        await send_message(
            phone,
            format_resume_ack(
                gate, task_id, approval, pr_review_mode=pr_review_mode
            ),
        )
    except Exception:
        logger.exception("Failed gate ack for task %s", task_id)

    try:
        from agent.agents.graph import resume_graph

        await resume_graph(
            task_id,
            approval,
            phone,
            gate=gate,
            pr_review_mode=pr_review_mode,
        )
        if gate == GATE_PLAN and approval == "rejected":
            await clear_workflow_session(phone)
    except Exception:
        logger.exception("Failed gate resume task %s gate=%s", task_id, gate)
        await send_message(phone, format_resume_failed(task_id))


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

    lock_key = ""
    acquired = False
    if approval == "approved":
        from agent.services.ci_gate import (
            check_deploy_blocked,
            deploy_lock_key,
            release_deploy,
            try_acquire_deploy,
        )

        ctx = await _pending_deploy_context(phone, task_id)
        blocked = await check_deploy_blocked(ctx, task_id=task_id)
        if blocked.busy:
            try:
                await send_message(phone, blocked.wait_message())
            except Exception:
                logger.exception("Failed to send CI-busy reply for task %s", task_id)
            return

        lock_key = deploy_lock_key(
            ctx.get("provider") or "",
            owner=ctx.get("owner") or "",
            repo_name=ctx.get("repo_name") or "",
            project=ctx.get("project") or "",
        )
        acquired = await try_acquire_deploy(lock_key, task_id)
        if not acquired:
            try:
                await send_message(
                    phone,
                    "A deploy for this repository is already in progress.\n"
                    "Please wait until it finishes, then try *Approve* again.",
                )
            except Exception:
                logger.exception("Failed to send deploy-lock reply for task %s", task_id)
            return

    try:
        if approval == "approved":
            await send_message(
                phone,
                f"Got it, Sunny — final approval received for `{task_id}`.\n"
                "CI is clear. Merging to main and deploying live…",
            )
        else:
            await send_message(
                phone,
                f"Got it — rejecting changes for `{task_id}`.",
            )
    except Exception:
        logger.exception("Failed to send approval ack for task %s", task_id)

    try:
        from agent.agents.graph import resume_graph

        await resume_graph(task_id, approval, phone)
    except Exception as exc:
        logger.exception("Failed to resume task %s with approval=%s", task_id, approval)
        from agent.services.deploy_notify import format_resume_deploy_failed, send_deploy_progress

        await send_deploy_progress(
            phone,
            format_resume_deploy_failed(
                task_id,
                f"Unexpected error after approval: {exc.__class__.__name__}. Reply *APPROVE {task_id}* to retry.",
            ),
        )
    finally:
        if acquired and lock_key:
            from agent.services.ci_gate import release_deploy

            await release_deploy(lock_key, task_id)
