"""QA Agent stub — Playwright runs land in a later phase.

Feature: Release Agent Fabric. ENABLE_QA_AGENT default false — no prod impact.
"""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.models.release_event import (
    STATUS_QA_FAILED,
    STATUS_QA_PASSED,
    STATUS_QA_PENDING,
    STATUS_QA_RUNNING,
    find_release_event,
    upsert_release_event,
)

logger = get_logger(__name__)

AGENT_NAME = "qa_agent"

_PR_RE = re.compile(r"\b(?:pr|pull\s*request)\s*#?\s*(\d+)\b", re.I)
_PIPELINE_RE = re.compile(r"\b(?:pipeline|build)\s*#?\s*(\d+)\b", re.I)
_RELEASE_RE = re.compile(r"\b(rel_[a-f0-9]+)\b", re.I)


async def run_qa_smoke(state: AgentState) -> AgentState:
    """Phase 3 will invoke Playwright; Phase 1 returns an honest stub response."""
    if not settings.enable_qa_agent:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*QA Agent* is installed but **disabled** (ENABLE_QA_AGENT=false).\n\n"
                "When enabled it will run Playwright against the release `app_url` "
                "and attach a report to the ReleaseEvent.\n"
                "Dev Agent and WhatsApp paths are unchanged."
            ),
        }

    msg = state.get("user_message") or ""
    pr = _PR_RE.search(msg)
    pipe = _PIPELINE_RE.search(msg)
    rel = _RELEASE_RE.search(msg)
    event = None
    if rel:
        from agent.models.release_event import get_release_event

        event = await get_release_event(rel.group(1))
    if event is None:
        event = await find_release_event(
            pr_id=pr.group(1) if pr else None,
            pipeline_id=pipe.group(1) if pipe else None,
            build_id=state.get("ci_build_id"),
        )

    if event is None:
        event = await upsert_release_event(
            pr_id=pr.group(1) if pr else None,
            pipeline_id=pipe.group(1) if pipe else None,
            app_url=state.get("app_url") or settings.agent_app_url,
            status=STATUS_QA_PENDING,
            requested_by=state.get("whatsapp_phone"),
            channel="teams" if str(state.get("whatsapp_phone") or "").startswith("teams:") else "whatsapp",
            title="QA pending",
            summary=msg[:1000],
        )

    await upsert_release_event(release_id=event["release_id"], status=STATUS_QA_RUNNING)

    # Phase 3: real Playwright. For now mark pending with clear next-step text.
    updated = await upsert_release_event(
        release_id=event["release_id"],
        status=STATUS_QA_PENDING,
        app_url=event.get("app_url") or settings.agent_app_url,
        summary=(event.get("summary") or "") + "\n[QA stub — Playwright not wired yet]",
    )

    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "release_id": updated["release_id"],
        "notification_text": (
            f"*QA Agent* accepted release `{updated['release_id']}`.\n\n"
            f"*App URL:* {updated.get('app_url') or 'n/a'}\n"
            f"*PR:* {updated.get('pr_id') or 'n/a'} · *Pipeline:* {updated.get('pipeline_id') or 'n/a'}\n\n"
            "_Playwright execution lands in Phase 3. Status set to "
            f"`{STATUS_QA_PENDING}` — Docs Agent can still draft notes on request._"
        ),
    }


# Silence unused import warnings for status constants used by later phases
_ = (STATUS_QA_PASSED, STATUS_QA_FAILED)
