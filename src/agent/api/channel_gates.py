"""Shared inbound gate routing for WhatsApp and Teams/Copilot channels.

WhatsApp webhook and Copilot API both call route_inbound_message so gate
behavior stays identical. Outbound delivery uses channel_notify (WhatsApp Graph
vs Teams outbox).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from agent.core.logging import get_logger

logger = get_logger(__name__)

ScheduleFn = Callable[..., Any]

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
FIX_TESTS_PATTERN = re.compile(
    r"(?i)^(?:"
    r"fix\s+tests?|fix\s+ci|repair\s+tests?|yes\s+fix|resolve(?:\s+it)?"
    r")(?:\s+([A-Za-z0-9_-]+))?[\s!.]*$"
)
SKIP_CI_FIX_PATTERN = re.compile(
    r"(?i)^(?:"
    r"skip(?:\s+fix)?|ignore(?:\s+failure)?|no\s+fix|leave\s+it"
    r")(?:\s+([A-Za-z0-9_-]+))?[\s!.]*$"
)


def task_id_from_match(match: re.Match | None) -> str | None:
    if not match:
        return None
    for group in match.groups():
        if group:
            return group
    return None


async def route_inbound_message(
    session_id: str,
    message: str,
    *,
    schedule: ScheduleFn,
    graph_payload: dict[str, Any] | None = None,
    source: str = "whatsapp",
) -> None:
    """Route gate / wizard / graph work for one inbound user message.

    `schedule(fn, *args, **kwargs)` queues work (BackgroundTasks for WhatsApp,
    or an await-collector for Copilot).
    """
    from agent.core.session import get_session
    from agent.workflow.gates import (
        GATE_CI_FIX,
        GATE_DEPLOY,
        GATE_MANUAL_PR,
        GATE_PIPELINE_WATCH,
        GATE_PLAN,
        GATE_PR_MODE,
        effective_awaiting,
        is_conversation_awaiting,
        is_workflow_gate,
        session_has_pr,
    )
    from agent.workflow.resume_context import (
        format_gate_hint,
        format_no_pending_task,
        format_session_status,
        is_cancel_session_message,
        is_restart_session_message,
        is_resume_status_message,
    )

    # Import WhatsApp helpers lazily — they use channel_notify for delivery.
    from agent.api import whatsapp as wa

    message = (message or "").strip()

    # Fabric Docs/QA/Knowledge intents bypass coding gates — must not become a code PR.
    import re

    if re.search(
        r"(release\s*notes|write\s+(?:the\s+)?docs?|publish\s+(?:to\s+)?sharepoint|"
        r"documentation\s+agent|create\s+(?:a\s+)?(?:release\s+)?document|"
        r"run\s+qa|start\s+qa|qa\s+agent|playwright|"
        r"list\s+(?:my\s+)?(?:documents?|docs|files)|list\s+(?:sharepoint|onedrive|onenote)|"
        r"list\s+ingested|ingest\s+(?:\d+|all)|vectorize|ask\s+docs?|ask\s+knowledge|"
        r"summarize\s+docs?|summarise\s+docs?|doc\s+insights?|knowledge\s+base)",
        message,
        re.I,
    ):
        from agent.core.session import save_session

        try:
            await save_session(session_id, awaiting=None, clear_awaiting=True, merge_data=True)
        except Exception:
            logger.exception("Failed clearing gate for fabric intent session=%s", session_id)
        payload = graph_payload or {
            "phone": session_id,
            "message": message,
            "message_id": "",
            "name": "",
        }
        payload = {**payload, "phone": session_id, "message": message}
        if source == "teams":
            from agent.core.background import handle_channel_message

            schedule(handle_channel_message, payload, source)
        else:
            from agent.core.background import handle_whatsapp_message

            schedule(handle_whatsapp_message, payload)
        return

    session = await get_session(session_id)
    # Ensure Teams sessions carry channel metadata for notify paths.
    if source == "teams" or str(session_id).startswith("teams:"):
        data = dict(session.get("data") or {})
        if data.get("channel") != "teams":
            from agent.core.session import save_session

            data["channel"] = "teams"
            data["teams_user_id"] = str(session_id).removeprefix("teams:")
            try:
                await save_session(session_id, data=data, merge_data=True)
                session = await get_session(session_id)
            except Exception:
                logger.exception("Failed tagging teams channel on session %s", session_id)

    proceed_match = PROCEED_PATTERN.match(message)
    reject_match = REJECT_PATTERN.match(message)
    approve_match = APPROVE_PATTERN.match(message)
    pr_ai_match = PR_AI_PATTERN.match(message)
    pr_manual_match = PR_MANUAL_PATTERN.match(message)
    pr_ready_match = PR_READY_PATTERN.match(message)
    fix_tests_match = FIX_TESTS_PATTERN.match(message)
    skip_ci_fix_match = SKIP_CI_FIX_PATTERN.match(message)

    needs_recover = bool(
        pr_ai_match
        or pr_manual_match
        or proceed_match
        or fix_tests_match
        or skip_ci_fix_match
        or (
            session.get("awaiting")
            in (GATE_PLAN, GATE_PR_MODE, GATE_CI_FIX, GATE_PIPELINE_WATCH)
            and session.get("data")
        )
    )
    if needs_recover:
        try:
            from agent.workflow.gate_recover import recover_session_for_gates

            session = await recover_session_for_gates(session)
        except Exception:
            logger.exception("Gate recovery failed for %s", session_id)

    awaiting = session.get("awaiting")
    session_data = dict(session.get("data") or {})
    session_provider = session.get("provider") or ""
    gate = effective_awaiting(session)
    has_pr = session_has_pr(session_data)
    logger.info(
        "Gate route session=%s source=%s msg=%r awaiting=%s gate=%s has_pr=%s task=%s",
        session_id,
        source,
        message[:40],
        awaiting,
        gate,
        has_pr,
        session_data.get("pending_task_id"),
    )

    payload = graph_payload or {
        "phone": session_id,
        "message": message,
        "message_id": "",
        "name": session_data.get("display_name") or "",
    }
    # Always align phone key for background/graph with session_id
    payload = {**payload, "phone": session_id, "message": message}

    if is_cancel_session_message(message):
        tid = session_data.get("pending_task_id") or ""
        schedule(wa._clear_and_notify, session_id, tid, awaiting or "")
        return

    if is_restart_session_message(message):
        tid = session_data.get("pending_task_id") or ""
        schedule(wa._clear_and_restart, session_id, message, tid, awaiting or "")
        return

    if is_resume_status_message(message):
        status_session = {**session, "awaiting": gate}
        schedule(
            wa._send_gate_hint,
            session_id,
            format_session_status(status_session) if gate else format_no_pending_task(),
        )
        return

    if gate == GATE_CI_FIX or (fix_tests_match and session_data.get("ci_build_id")):
        if fix_tests_match or (
            message.lower().strip() in ("yes", "y", "ok", "okay", "fix") and gate == GATE_CI_FIX
        ):
            tid = task_id_from_match(fix_tests_match) or session_data.get("pending_task_id")
            schedule(wa._run_ci_test_fixer, session_id, session_data, session_provider, tid)
            return
        if skip_ci_fix_match or reject_match:
            schedule(wa._skip_ci_test_fix, session_id, session_data, session_provider)
            return
        if gate == GATE_CI_FIX:
            schedule(
                wa._send_gate_hint,
                session_id,
                format_gate_hint(GATE_CI_FIX, session_data, session_provider),
            )
            return

    if gate == GATE_PIPELINE_WATCH:
        schedule(
            wa._send_gate_hint,
            session_id,
            format_gate_hint(GATE_PIPELINE_WATCH, session_data, session_provider),
        )
        return

    if (pr_ai_match or pr_manual_match) and (gate == GATE_PR_MODE or has_pr):
        mode = "ai" if pr_ai_match else "manual"
        tid = task_id_from_match(pr_ai_match or pr_manual_match)
        if gate != GATE_PR_MODE or not has_pr:
            schedule(
                wa._heal_pr_mode_and_resume,
                session_id,
                session_data,
                session_provider,
                mode,
                tid,
            )
        else:
            schedule(
                wa._resume_gate,
                GATE_PR_MODE,
                "approved",
                session_id,
                tid,
                pr_review_mode=mode,
            )
        return

    if (pr_ai_match or pr_manual_match) and gate == GATE_PLAN and session_data.get("pending_task_id"):
        schedule(
            wa._send_gate_hint,
            session_id,
            "No pull request is ready for review yet for task "
            f"`{session_data.get('pending_task_id')}`.\n\n"
            "If development is still running, wait for the *PR opened* message, "
            "then reply *1* for AI review.\n"
            "Or reply *stop* to cancel.",
        )
        return

    if (proceed_match or approve_match) and gate == GATE_PR_MODE:
        schedule(
            wa._send_gate_hint,
            session_id,
            format_gate_hint(GATE_PR_MODE, session_data, session_provider),
        )
        return

    if proceed_match and gate in (GATE_PLAN, None, ""):
        tid = task_id_from_match(proceed_match) or session_data.get("pending_task_id")
        if gate == GATE_PLAN or tid:
            schedule(wa._resume_gate, GATE_PLAN, "approved", session_id, tid)
            return
        schedule(
            wa._send_gate_hint,
            session_id,
            "To continue the plan, reply *PROCEED <task_id>* "
            "(use the id from the Implementation plan message).\n"
            "Or say *check my repos* to start fresh.",
        )
        return

    if gate == GATE_PLAN:
        if reject_match:
            schedule(
                wa._resume_gate,
                GATE_PLAN,
                "rejected",
                session_id,
                task_id_from_match(reject_match),
            )
        elif approve_match and (
            message.lower().strip() in ("approve", "approved") or "plan" in message.lower()
        ):
            tid = task_id_from_match(approve_match) or session_data.get("pending_task_id")
            schedule(wa._resume_gate, GATE_PLAN, "approved", session_id, tid)
        else:
            schedule(
                wa._send_gate_hint,
                session_id,
                format_gate_hint(GATE_PLAN, session_data, session_provider),
            )
        return

    if gate == GATE_PR_MODE:
        schedule(
            wa._send_gate_hint,
            session_id,
            format_gate_hint(GATE_PR_MODE, session_data, session_provider),
        )
        return

    if gate == GATE_MANUAL_PR:
        if pr_ready_match:
            schedule(
                wa._resume_gate,
                GATE_MANUAL_PR,
                "approved",
                session_id,
                task_id_from_match(pr_ready_match),
            )
        else:
            schedule(
                wa._send_gate_hint,
                session_id,
                format_gate_hint(GATE_MANUAL_PR, session_data, session_provider),
            )
        return

    if is_conversation_awaiting(awaiting):
        if source == "teams":
            from agent.core.background import handle_channel_message

            schedule(handle_channel_message, payload, source)
        else:
            from agent.core.background import handle_whatsapp_message

            schedule(handle_whatsapp_message, payload)
        return

    if approve_match and gate in (GATE_DEPLOY, "approval", None):
        schedule(
            wa._resume_with_approval,
            task_id_from_match(approve_match),
            "approved",
            session_id,
        )
        return

    if reject_match and gate in (GATE_DEPLOY, "approval", None):
        schedule(
            wa._resume_with_approval,
            task_id_from_match(reject_match),
            "rejected",
            session_id,
        )
        return

    if is_workflow_gate(gate):
        schedule(
            wa._send_gate_hint,
            session_id,
            format_gate_hint(gate, session_data, session_provider),
        )
        return

    if source == "teams":
        from agent.core.background import handle_channel_message

        schedule(handle_channel_message, payload, source)
    else:
        from agent.core.background import handle_whatsapp_message

        schedule(handle_whatsapp_message, payload)
