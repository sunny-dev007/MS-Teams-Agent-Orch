"""Orbit (Teams Bot) proactive delivery — outbox flush + progress heartbeats.

Copilot Studio still polls the outbox via action=status / action=poll.
Orbit has conversation references and can push updates without the user typing
"status". This module only targets teams: sessions.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from agent.config import settings
from agent.core.channel_identity import is_teams_session
from agent.core.logging import get_logger
from agent.workflow.gates import WORKFLOW_GATES

logger = get_logger(__name__)

# Gates / awaiting values where a long background job is likely still running.
_PROGRESS_GATES = frozenset(WORKFLOW_GATES) | {
    "pipeline_watching",
    "docs_ingest",
    "boards_handoff",
}


async def flush_orbit_outbox_once(*, min_age_seconds: int | None = None) -> dict[str, Any]:
    """Push undelivered Teams outbox rows via Bot Framework proactive send.

    Skips very fresh rows so an active Orbit turn can drain them first
    (avoids duplicate delivery).
    """
    if not getattr(settings, "enable_teams_bot_channel", False):
        return {"skipped": True, "reason": "teams_bot_disabled"}
    if not getattr(settings, "enable_orbit_outbox_flusher", True):
        return {"skipped": True, "reason": "flusher_disabled"}

    from agent.core.channel_outbox import (
        list_pending_outbox_sessions,
        mark_outbox_ids_delivered,
        peek_outbox_texts,
    )
    from agent.services.teams_proactive import try_send_proactive_teams_message

    age = (
        int(min_age_seconds)
        if min_age_seconds is not None
        else max(12, int(getattr(settings, "orbit_outbox_flush_seconds", 20) or 20) - 5)
    )
    sessions = await list_pending_outbox_sessions(prefix="teams:", limit=40)
    pushed = 0
    failed = 0
    for session_id in sessions:
        rows = await peek_outbox_texts(
            session_id, limit=15, min_age_seconds=age
        )
        if not rows:
            continue
        delivered_ids: list[int] = []
        for oid, text in rows:
            ok = await try_send_proactive_teams_message(session_id, text)
            if ok:
                delivered_ids.append(oid)
                pushed += 1
            else:
                failed += 1
                # Preserve order — retry the rest next tick.
                break
        if delivered_ids:
            await mark_outbox_ids_delivered(delivered_ids)
            logger.info(
                "Orbit outbox flush session=%s delivered=%s",
                session_id,
                len(delivered_ids),
            )
    return {"sessions": len(sessions), "pushed": pushed, "failed": failed}


async def tick_orbit_progress_once() -> dict[str, Any]:
    """Heartbeat while a long Dev Agent / coding task is in flight.

    Does not replace milestone outbox messages — only reminds the user that
    Orbit is still working so the chat does not feel stuck.
    """
    if not getattr(settings, "enable_teams_bot_channel", False):
        return {"skipped": True, "reason": "teams_bot_disabled"}
    if not getattr(settings, "enable_orbit_progress_ticker", True):
        return {"skipped": True, "reason": "progress_disabled"}

    from agent.core.channel_outbox import list_pending_outbox_sessions, peek_outbox_count
    from agent.core.session import get_session, list_active_teams_sessions, save_session
    from agent.services.teams_proactive import (
        load_conversation_reference,
        try_send_proactive_teams_message,
    )

    sent = 0
    checked = 0
    interval = max(45, int(getattr(settings, "orbit_progress_ticker_seconds", 90) or 90))
    now = time.time()

    candidate_ids: set[str] = set(
        await list_pending_outbox_sessions(prefix="teams:", limit=40)
    )
    try:
        candidate_ids.update(await list_active_teams_sessions(limit=40))
    except Exception:
        logger.exception("orbit progress: list_active_teams_sessions failed")

    for session_id in list(candidate_ids):
        if not is_teams_session(session_id):
            continue
        checked += 1
        try:
            session = await get_session(session_id)
        except Exception:
            continue
        data = dict(session.get("data") or {})
        gate = str(session.get("awaiting") or "").strip()
        tid = str(data.get("pending_task_id") or "").strip()
        if not tid and gate not in _PROGRESS_GATES:
            continue
        if gate and gate not in _PROGRESS_GATES and not tid:
            continue

        ref = await load_conversation_reference(session_id)
        if not ref:
            continue

        last = float(data.get("orbit_last_progress_at") or 0)
        if last and (now - last) < interval:
            continue

        # Prefer real milestone messages over heartbeats.
        if await peek_outbox_count(session_id) > 0:
            continue

        short = tid[:8] if tid else "task"
        text = (
            f"*Orbit* — still working on `{short}`…\n\n"
            "_You'll get the next update here automatically when this step finishes._"
        )
        ok = await try_send_proactive_teams_message(session_id, text)
        if ok:
            sent += 1
            try:
                await save_session(
                    session_id,
                    data={"orbit_last_progress_at": now},
                    merge_data=True,
                )
            except Exception:
                logger.exception(
                    "orbit progress: failed persisting heartbeat ts session=%s",
                    session_id,
                )

    return {"checked": checked, "sent": sent}


async def run_orbit_delivery_loop() -> None:
    """Background loop: flush outbox often; progress heartbeat less often."""
    flush_every = max(10, int(getattr(settings, "orbit_outbox_flush_seconds", 20) or 20))
    progress_every = max(45, int(getattr(settings, "orbit_progress_ticker_seconds", 90) or 90))
    logger.info(
        "Orbit delivery loop started flush=%ss progress=%ss",
        flush_every,
        progress_every,
    )
    last_progress = 0.0
    while True:
        try:
            await flush_orbit_outbox_once()
        except Exception:
            logger.exception("Orbit outbox flush tick failed")
        now = time.time()
        if now - last_progress >= progress_every:
            last_progress = now
            try:
                await tick_orbit_progress_once()
            except Exception:
                logger.exception("Orbit progress tick failed")
        await asyncio.sleep(flush_every)
