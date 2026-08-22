"""Doc Insights Agent — executive summarization over ingested knowledge.

Separate from Doc RAG so QnA retrieval and insight synthesis stay isolated.
Feature: Document Knowledge Fabric. ENABLE_DOC_KNOWLEDGE default false.
"""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.services import doc_knowledge

logger = get_logger(__name__)

AGENT_NAME = "doc_insights_agent"

_STRIP_PREFIX = re.compile(
    r"^\s*(?:summarize\s+docs?|doc\s+insights?|insights?\s+(?:on|from)\s+docs?|"
    r"summarise\s+docs?)\s*[:\-]?\s*",
    re.I,
)


async def summarize_documents(state: AgentState) -> AgentState:
    if not settings.enable_doc_knowledge:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Doc Insights Agent* is installed but **disabled** "
                "(ENABLE_DOC_KNOWLEDGE=false).\n"
                "Existing Dev/WhatsApp flows are unchanged."
            ),
        }

    raw = state.get("user_message") or ""

    from agent.services import doc_upload as _doc_upload

    if _doc_upload.message_expects_teams_attachment(raw):
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                _doc_upload.attachment_not_received_reply()
                + "\n\n_I did not summarize the existing knowledge base — that would be misleading._"
            ),
            "error": "attachment_bytes_missing",
        }

    focus = _STRIP_PREFIX.sub("", raw).strip() or "overall themes and risks"
    try:
        result = await doc_knowledge.summarize_insights(focus, owner_session=None)
    except Exception as exc:
        logger.exception("Doc Insights failed")
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": f"*Doc Insights Agent* failed: {exc}",
            "error": str(exc),
        }

    used = result.get("docs_used") or []
    used_line = ", ".join(f"{d.get('title')}" for d in used[:8]) if used else "none"
    note = (
        f"*Doc Insights Agent*\n\n{result.get('summary') or '_No summary_'}\n\n"
        f"_Based on:_ {used_line}"
    )
    # Related prompts from docs used (titles → grounded follow-ups)
    related = []
    for d in used[:3]:
        t = (d.get("title") or "").strip()
        if t:
            related.append(f"ask docs what are the key points in {t}?")
    if related:
        note += "\n\n*Related*\n" + "\n".join(f"• `{q}`" for q in related)

    phone = state.get("whatsapp_phone") or ""
    if phone:
        try:
            from agent.services.workspace_handoff import WS_KNOWLEDGE, mark_workspace

            await mark_workspace(phone, WS_KNOWLEDGE)
        except Exception:
            logger.exception("Failed marking knowledge workspace")

    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "notification_text": note,
        "kb_docs_used": used,
    }
