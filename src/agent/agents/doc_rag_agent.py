"""Doc RAG Agent — retrieve across ingested documents and answer with citations.

Feature: Document Knowledge Fabric. ENABLE_DOC_KNOWLEDGE default false.
"""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.services import doc_knowledge

logger = get_logger(__name__)

AGENT_NAME = "doc_rag_agent"

_STRIP_PREFIX = re.compile(
    r"^\s*(?:ask\s+docs?|ask\s+knowledge|doc\s+q(?:na|&a)?|question\s+on\s+docs?)\s*[:\-]?\s*",
    re.I,
)


async def ask_documents(state: AgentState) -> AgentState:
    if not settings.enable_doc_knowledge:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Doc RAG Agent* is installed but **disabled** "
                "(ENABLE_DOC_KNOWLEDGE=false).\n"
                "Existing Dev/WhatsApp flows are unchanged."
            ),
        }

    raw = state.get("user_message") or ""
    question = _STRIP_PREFIX.sub("", raw).strip() or raw.strip()
    if not question:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": "Ask like: *ask docs what is our release process?*",
        }

    phone = state.get("whatsapp_phone") or ""
    try:
        result = await doc_knowledge.retrieve_answer(question, owner_session=None)
        # Prefer session-scoped first; if empty, retrieve_answer already searched global.
        if phone and not (result.get("citations")):
            result = await doc_knowledge.retrieve_answer(question, owner_session=phone)
    except Exception as exc:
        logger.exception("Doc RAG failed")
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": f"*Doc RAG Agent* failed: {exc}",
            "error": str(exc),
        }

    cites = result.get("citations") or []
    cite_lines = []
    for c in cites:
        url = c.get("web_url") or ""
        link = f" — {url}" if url else ""
        cite_lines.append(
            f"[{c.get('n')}] {c.get('title')} "
            f"({c.get('source_type')}/{c.get('doc_mode')}, score={c.get('score')}){link}"
        )

    note = f"*Doc RAG Agent*\n\n{result.get('answer') or '_No answer_'}\n"
    if cite_lines:
        note += "\n*Sources*\n" + "\n".join(cite_lines)

    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "notification_text": note,
        "kb_citations": cites,
    }
