"""Optional Dev → fabric handoff after deploy/CI success.

Only runs when ENABLE_RELEASE_HANDOFF=true. Default false = zero prod impact.
"""

from __future__ import annotations

from typing import Any

from agent.config import settings
from agent.core.logging import get_logger
from agent.models.release_event import STATUS_DEPLOYED, STATUS_QA_PENDING, upsert_release_event
from agent.services.channel_notify import send_channel_message

logger = get_logger(__name__)


async def emit_release_handoff(
    *,
    phone: str,
    pr_id: str | int | None = None,
    pipeline_id: str | int | None = None,
    build_id: str | int | None = None,
    commit_sha: str | None = None,
    app_url: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    channel: str | None = None,
) -> dict[str, Any] | None:
    """Upsert ReleaseEvent and optionally nudge the user about QA / Docs."""
    if not settings.enable_release_handoff:
        return None

    try:
        ch = channel or ("teams" if str(phone or "").startswith("teams:") else "whatsapp")
        event = await upsert_release_event(
            pr_id=str(pr_id) if pr_id is not None else None,
            pipeline_id=str(pipeline_id) if pipeline_id is not None else None,
            build_id=str(build_id) if build_id is not None else None,
            commit_sha=commit_sha,
            app_url=app_url or settings.agent_app_url,
            status=STATUS_QA_PENDING if settings.enable_qa_agent else STATUS_DEPLOYED,
            requested_by=phone,
            channel=ch,
            title=title or "Deployment succeeded",
            summary=summary,
        )
        if phone:
            lines = [
                "*Release Agent Fabric* — deployment recorded.",
                f"*Release:* `{event['release_id']}`",
                f"*PR:* {event.get('pr_id') or 'n/a'} · *Build:* {event.get('build_id') or 'n/a'}",
            ]
            if settings.enable_qa_agent:
                lines.append(
                    f"Reply *run QA for {event['release_id']}* to start the QA Agent."
                )
            if settings.enable_docs_agent:
                lines.append(
                    f"Reply *write release notes for {event['release_id']}* for the Docs Agent."
                )
            await send_channel_message(phone, "\n".join(lines))
        return event
    except Exception:
        logger.exception("emit_release_handoff failed (ignored — must not break Dev path)")
        return None
