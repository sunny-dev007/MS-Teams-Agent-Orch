"""Agent run orchestration — busy locks, soft queue, status, recent bubbles.

Feature: ENABLE_AGENT_RUN_ORCHESTRATION (default False — opt-in App Setting).
Does not change coding WORKFLOW_GATES, fabric bypass, or FinOps APPLY PLAN.

Goals:
- Same agent already running → ask user to wait (+ soft-queue); other agents OK
- status shows active / queued / recent runs
- Keep last N outbound bubbles for context (default 20)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

_KEY_RUNS = "agent_active_runs"
_KEY_QUEUE = "agent_run_queue"
_KEY_BUBBLES = "agent_recent_bubbles"
_KEY_HISTORY = "agent_run_history"

_AGENT_LABELS: dict[str, str] = {
    "azure_finops": "Azure FinOps Agent",
    "azure_finops_agent": "Azure FinOps Agent",
    "check_outlook": "Outlook Agent",
    "outlook_agent": "Outlook Agent",
    "list_boards": "Boards Agent",
    "boards_agent": "Boards Agent",
    "boards_start_dev": "Boards Agent",
    "list_docs": "Doc Knowledge",
    "ingest_docs": "Doc Knowledge",
    "ask_docs": "Doc Knowledge",
    "code_change": "Dev Agent",
    "bug_fix": "Dev Agent",
    "task_status": "Task Status",
}

_AWAITING_AGENT: dict[str, str] = {
    "azure_finops_sub_pick": "azure_finops",
    "azure_finops_action_pick": "azure_finops",
    "azure_finops_rg_pick": "azure_finops",
    "azure_finops_mutate_plan": "azure_finops",
}


def orchestration_enabled() -> bool:
    return bool(getattr(settings, "enable_agent_run_orchestration", False))


def agent_label(agent_key: str) -> str:
    key = (agent_key or "").strip()
    return _AGENT_LABELS.get(key) or key.replace("_", " ").title() or "Agent"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _bubble_limit() -> int:
    return max(5, min(40, int(getattr(settings, "agent_recent_bubbles", 20) or 20)))


def _runs_from_session(session: dict[str, Any]) -> list[dict[str, Any]]:
    data = session.get("data") or {}
    runs = data.get(_KEY_RUNS) or []
    return list(runs) if isinstance(runs, list) else []


def _queue_from_session(session: dict[str, Any]) -> list[dict[str, Any]]:
    data = session.get("data") or {}
    queue = data.get(_KEY_QUEUE) or []
    return list(queue) if isinstance(queue, list) else []


def find_busy_same_agent(
    session: dict[str, Any], *, agent_key: str
) -> dict[str, Any] | None:
    if not orchestration_enabled() or not agent_key:
        return None
    key = agent_key.strip()
    for run in _runs_from_session(session):
        if (run.get("agent") or "") == key:
            return run
    return None


def format_busy_wait(
    run: dict[str, Any],
    *,
    other_ok: bool = True,
    queued: bool = False,
) -> str:
    label = agent_label(str(run.get("agent") or ""))
    task = run.get("task_id") or "—"
    started = run.get("started_at") or ""
    lines = [
        f"*{label}* is still working on your previous request.",
        "",
        f"• Task: `{task}`",
    ]
    if started:
        lines.append(f"• Started: `{started}`")
    if queued:
        lines.extend(
            [
                "",
                "Your latest message was **queued** for this agent. "
                "It will not auto-start — say **status**, then resend when free.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Please wait for that reply — then ask again on this agent.",
            ]
        )
    if other_ok:
        lines.append(
            "You *can* ask a **different** agent now "
            "(Outlook / Boards / Docs / Dev) without losing FinOps/coding gates."
        )
    lines.extend(["", "Say **status** to see active / queued agents."])
    return "\n".join(lines)


async def register_run(
    phone: str,
    *,
    agent_key: str,
    task_id: str,
    label: str = "",
    user_message: str = "",
) -> None:
    if not orchestration_enabled() or not phone or not agent_key:
        return
    try:
        from agent.core.session import get_session, save_session

        session = await get_session(phone)
        data = dict(session.get("data") or {})
        runs = [
            r
            for r in list(data.get(_KEY_RUNS) or [])
            if (r.get("agent") or "") != agent_key
        ]
        runs.append(
            {
                "agent": agent_key,
                "task_id": task_id,
                "label": label or agent_label(agent_key),
                "started_at": _now_iso(),
                "preview": (user_message or "")[:120],
            }
        )
        data[_KEY_RUNS] = runs[-8:]
        data[_KEY_QUEUE] = [
            q
            for q in list(data.get(_KEY_QUEUE) or [])
            if (q.get("agent") or "") != agent_key
        ]
        await save_session(phone, data=data, merge_data=True)
    except Exception:
        logger.exception("register_run failed phone=%s agent=%s", phone, agent_key)


async def finish_run(phone: str, *, agent_key: str = "", task_id: str = "") -> None:
    if not orchestration_enabled() or not phone:
        return
    try:
        from agent.core.session import get_session, save_session

        session = await get_session(phone)
        data = dict(session.get("data") or {})
        runs = list(data.get(_KEY_RUNS) or [])
        finished: list[dict[str, Any]] = []
        kept: list[dict[str, Any]] = []
        clear_all = not agent_key and not task_id
        for r in runs:
            match_agent = bool(agent_key) and (r.get("agent") or "") == agent_key
            match_task = bool(task_id) and (r.get("task_id") or "") == task_id
            if clear_all or match_agent or match_task:
                finished.append({**r, "finished_at": _now_iso()})
            else:
                kept.append(r)
        data[_KEY_RUNS] = [] if clear_all else kept
        history = list(data.get(_KEY_HISTORY) or [])
        history.extend(finished)
        data[_KEY_HISTORY] = history[-20:]
        await save_session(phone, data=data, merge_data=True)
    except Exception:
        logger.exception("finish_run failed phone=%s", phone)


async def enqueue_run(
    phone: str,
    *,
    agent_key: str,
    user_message: str,
    task_id: str = "",
) -> None:
    """Soft-queue a same-agent request (display only; does not auto-run)."""
    if not orchestration_enabled() or not phone or not agent_key:
        return
    try:
        from agent.core.session import get_session, save_session

        session = await get_session(phone)
        data = dict(session.get("data") or {})
        queue = list(data.get(_KEY_QUEUE) or [])
        queue.append(
            {
                "agent": agent_key,
                "label": agent_label(agent_key),
                "preview": (user_message or "")[:120],
                "task_id": task_id or "",
                "queued_at": _now_iso(),
            }
        )
        data[_KEY_QUEUE] = queue[-10:]
        await save_session(phone, data=data, merge_data=True)
    except Exception:
        logger.exception("enqueue_run failed phone=%s agent=%s", phone, agent_key)


async def append_bubble(
    phone: str,
    *,
    role: str,
    text: str,
    agent: str = "",
) -> None:
    if not orchestration_enabled() or not phone or not text:
        return
    try:
        from agent.core.session import get_session, save_session

        session = await get_session(phone)
        data = dict(session.get("data") or {})
        bubbles = list(data.get(_KEY_BUBBLES) or [])
        bubbles.append(
            {
                "role": role,
                "text": (text or "")[:1500],
                "agent": agent or "",
                "ts": _now_iso(),
            }
        )
        data[_KEY_BUBBLES] = bubbles[-_bubble_limit() :]
        await save_session(phone, data=data, merge_data=True)
    except Exception:
        logger.exception("append_bubble failed phone=%s", phone)


def recent_bubbles(session: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    data = session.get("data") or {}
    bubbles = data.get(_KEY_BUBBLES) or []
    if not isinstance(bubbles, list):
        return []
    return bubbles[-max(1, limit) :]


def format_orchestration_status(session: dict[str, Any]) -> str:
    if not orchestration_enabled():
        return ""
    runs = _runs_from_session(session)
    queue = _queue_from_session(session)
    data = session.get("data") or {}
    history = data.get(_KEY_HISTORY) or []
    bubbles = data.get(_KEY_BUBBLES) or []
    lines = ["*Agent runtime*", ""]
    if runs:
        lines.append("**Running now**")
        for r in runs:
            lines.append(
                f"• {r.get('label') or agent_label(str(r.get('agent') or ''))} "
                f"(`{r.get('task_id')}`) since {r.get('started_at') or '—'}"
            )
            if r.get("preview"):
                lines.append(f"  _{(r.get('preview') or '')[:80]}_")
        lines.append("")
    else:
        lines.append("_No agent is running right now._")
        lines.append("")
    if queue:
        lines.append("**Queued** (waiting — resend when that agent is free)")
        for q in queue[-8:]:
            lines.append(
                f"• {q.get('label') or agent_label(str(q.get('agent') or ''))} "
                f"— _{(q.get('preview') or '')[:80] or '—'}_"
            )
        lines.append("")
    if isinstance(history, list) and history:
        lines.append("**Recently finished**")
        for r in list(history)[-5:]:
            lines.append(
                f"• {r.get('label') or agent_label(str(r.get('agent') or ''))} "
                f"(`{r.get('task_id')}`) — done {r.get('finished_at') or '—'}"
            )
        lines.append("")
    n = len(bubbles) if isinstance(bubbles, list) else 0
    lines.append(f"_Conversation buffer: last **{n}** bubbles kept for context._")
    lines.append(
        "_Tip:_ same agent busy → wait; other agents (Outlook / Boards / Docs) can still run."
    )
    return "\n".join(lines)


def infer_agent_key_from_message(message: str) -> str:
    msg = (message or "").lower()
    if any(
        k in msg
        for k in (
            "azure",
            "finops",
            "subscription",
            "deep scan",
            "cost",
            "resource group",
        )
    ):
        return "azure_finops"
    if any(k in msg for k in ("outlook", "inbox", "email", "mail")):
        return "check_outlook"
    if any(k in msg for k in ("work item", "boards", "ticket", "my tickets")):
        return "list_boards"
    if any(k in msg for k in ("document", "sharepoint", "ingest", "ask docs")):
        return "list_docs"
    if any(k in msg for k in ("repo", "pr ", "pull request", "deploy", "code")):
        return "code_change"
    return ""


def infer_agent_key_from_session(session: dict[str, Any]) -> str:
    awaiting = (session.get("awaiting") or "").strip()
    if awaiting in _AWAITING_AGENT:
        return _AWAITING_AGENT[awaiting]
    if awaiting.startswith("azure_finops"):
        return "azure_finops"
    if awaiting.startswith("outlook"):
        return "check_outlook"
    if awaiting.startswith("boards"):
        return "list_boards"
    if awaiting.startswith("doc"):
        return "list_docs"
    return ""


def resolve_agent_key(session: dict[str, Any], message: str) -> str:
    """Prefer live awaiting agent, else message heuristics."""
    return infer_agent_key_from_session(session) or infer_agent_key_from_message(
        message
    )
