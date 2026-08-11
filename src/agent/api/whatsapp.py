from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from agent.api.channel_gates import (
    APPROVE_PATTERN,
    FIX_TESTS_PATTERN,
    PROCEED_PATTERN,
    PR_AI_PATTERN,
    PR_MANUAL_PATTERN,
    PR_READY_PATTERN,
    REJECT_PATTERN,
    SKIP_CI_FIX_PATTERN,
    route_inbound_message,
    task_id_from_match,
)
from agent.config import settings
from agent.core.logging import get_logger
from agent.core.security import is_phone_allowed, verify_whatsapp_signature
from agent.services.whatsapp import parse_incoming_message

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])

# Re-export for existing tests / callers
_task_id_from_match = task_id_from_match


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

    await route_inbound_message(
        parsed["phone"],
        message,
        schedule=background_tasks.add_task,
        graph_payload=parsed,
        source="whatsapp",
    )
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
    from agent.services.channel_notify import send_channel_message
    from agent.workflow.resume_context import format_session_cleared

    await clear_session(phone)
    try:
        await send_channel_message(phone, format_session_cleared(had_task_id=task_id, had_gate=awaiting))
    except Exception:
        logger.exception("Failed cancel notify for %s", phone)


async def _clear_and_restart(phone: str, message: str, task_id: str, awaiting: str) -> None:
    from agent.core.session import clear_session
    from agent.services.channel_notify import send_channel_message
    from agent.workflow.resume_context import format_session_cleared

    await clear_session(phone)
    try:
        if task_id or awaiting:
            await send_channel_message(
                phone,
                format_session_cleared(had_task_id=task_id, had_gate=awaiting) + "\n\n_Starting fresh…_",
            )
    except Exception:
        logger.exception("Failed restart notify for %s", phone)

    from agent.core.background import handle_channel_message

    await handle_channel_message({"phone": phone, "message": message}, source="whatsapp")


async def _send_gate_hint(phone: str, text: str) -> None:
    from agent.services.channel_notify import send_channel_message

    try:
        await send_channel_message(phone, text)
    except Exception:
        logger.exception("Failed to send gate hint to %s", phone)


async def _run_ci_test_fixer(
    phone: str,
    session_data: dict,
    session_provider: str,
    explicit_task_id: str | None,
) -> None:
    """Sunny approved AI repair of failed CI tests — run test_fixer, keep context."""
    from agent.services.channel_notify import send_channel_message
    from agent.specialists.notifier_agent import run_notifier
    from agent.specialists.test_fixer import run_test_fixer

    task_id = (
        (explicit_task_id or "").strip()
        or str(session_data.get("pending_task_id") or "")
    )
    if not task_id:
        await send_channel_message(phone, "No task id for CI fix. Reply *STOP* or *check my repos*.")
        return

    try:
        await send_channel_message(
            phone,
            f"*Sunny's AI Agent* — Test fixer started (`{task_id}`)\n\n"
            "I'm reading the pipeline failure logs and preparing a focused fix. "
            "Your conversation context stays until you say *STOP* or *check my repos*.",
        )
    except Exception:
        logger.exception("Failed CI-fix ack for %s", phone)

    state = {
        "task_id": task_id,
        "whatsapp_phone": phone,
        "repo_provider": session_provider or session_data.get("repo_provider") or "azure_devops",
        **session_data,
        "pending_task_id": task_id,
    }
    try:
        result = await run_test_fixer(state)
        result = await run_notifier(result)
        text = result.get("notification_text") or "Test fixer finished."
        await send_channel_message(phone, text)
    except Exception:
        logger.exception("CI test fixer failed for task %s", task_id)
        try:
            await send_channel_message(
                phone,
                f"Test fixer hit an unexpected error for `{task_id}`.\n"
                "Reply *FIX TESTS* to retry, *SKIP*, or *STOP*.",
            )
        except Exception:
            logger.exception("Failed test-fixer error notify")


async def _skip_ci_test_fix(
    phone: str,
    session_data: dict,
    session_provider: str,
) -> None:
    """Leave CI failure as-is but keep task memory until STOP."""
    from agent.services.channel_notify import send_channel_message
    from agent.workflow.gates import persist_deploy_gate, persist_pr_mode_gate, session_has_pr
    from agent.workflow.resume_context import format_gate_hint

    tid = session_data.get("pending_task_id") or ""
    provider = session_provider or session_data.get("repo_provider") or ""
    data = {**session_data, "ci_fix_skipped": True, "pending_task_id": tid}
    try:
        if session_has_pr(data):
            await persist_pr_mode_gate(
                phone,
                {
                    "task_id": tid,
                    "repo_provider": provider,
                    **data,
                },
            )
            await send_channel_message(
                phone,
                f"OK — skipped AI test repair for `{tid}`.\n\n"
                + format_gate_hint("pr_review_mode", data, provider),
            )
        elif data.get("pr_url") or data.get("file_changes"):
            await persist_deploy_gate(
                phone,
                {"task_id": tid, "repo_provider": provider, **data},
            )
            await send_channel_message(
                phone,
                f"OK — skipped AI test repair for `{tid}`.\n\n"
                "You can still *APPROVE* / *REJECT* deploy, or *STOP* to clear.",
            )
        else:
            from agent.core.session import save_session

            await save_session(
                phone,
                awaiting=None,
                clear_awaiting=True,
                provider=provider,
                data=data,
                merge_data=False,
            )
            await send_channel_message(
                phone,
                f"OK — left the CI failure as-is for `{tid}`.\n\n"
                "Task context is still remembered. Reply *STOP* to clear, "
                "or *check my repos* to start a new coding task.",
            )
    except Exception:
        logger.exception("Failed skipping CI fix for %s", phone)


async def _heal_pr_mode_and_resume(
    phone: str,
    session_data: dict,
    session_provider: str,
    pr_review_mode: str,
    explicit_task_id: str | None,
) -> None:
    """Fix stale plan_approval session after PR was already opened, then run AI/manual review."""
    from agent.workflow.gates import GATE_PR_MODE, persist_pr_mode_gate

    state = {
        "task_id": explicit_task_id or session_data.get("pending_task_id") or "",
        "repo_provider": session_provider or session_data.get("repo_provider") or "",
        **session_data,
    }
    try:
        await persist_pr_mode_gate(phone, state)
    except Exception:
        logger.exception("Failed healing PR-mode session for %s", phone)
    await _resume_gate(
        GATE_PR_MODE,
        "approved",
        phone,
        explicit_task_id or state.get("task_id"),
        pr_review_mode=pr_review_mode,
    )


async def _resume_gate(
    gate: str,
    approval: str,
    phone: str,
    explicit_task_id: str | None,
    *,
    pr_review_mode: str | None = None,
) -> None:
    from agent.services.channel_notify import send_channel_message
    from agent.workflow.gates import GATE_MANUAL_PR, GATE_PLAN, GATE_PR_MODE, clear_workflow_session
    from agent.workflow.resume_context import format_resume_ack, format_resume_failed

    task_id = await _resolve_task_id(phone, explicit_task_id)
    if not task_id:
        from agent.workflow.resume_context import format_no_pending_task

        await send_channel_message(phone, format_no_pending_task())
        return

    try:
        await send_channel_message(
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
        await send_channel_message(phone, format_resume_failed(task_id))


async def _resume_with_approval(
    explicit_task_id: str | None, approval: str, phone: str
) -> None:
    from agent.services.channel_notify import send_channel_message

    task_id = await _resolve_task_id(phone, explicit_task_id)
    if not task_id:
        try:
            await send_channel_message(
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
                await send_channel_message(phone, blocked.wait_message())
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
                await send_channel_message(
                    phone,
                    "A deploy for this repository is already in progress.\n"
                    "Please wait until it finishes, then try *Approve* again.",
                )
            except Exception:
                logger.exception("Failed to send deploy-lock reply for task %s", task_id)
            return

    try:
        if approval == "approved":
            await send_channel_message(
                phone,
                f"Got it, Sunny — final approval received for `{task_id}`.\n"
                "CI is clear. Merging to main and deploying live…",
            )
        else:
            await send_channel_message(
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
