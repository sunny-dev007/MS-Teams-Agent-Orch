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
    from agent.services.graph_docs import source_tag

    for c in cites:
        url = c.get("web_url") or ""
        link = f" — {url}" if url else ""
        locator = (c.get("locator") or "").strip()
        loc = f" · {locator}" if locator else ""
        tag = source_tag(c.get("source_type"))
        cite_lines.append(
            f"[{c.get('n')}] [{tag}] {c.get('title')}{loc} "
            f"({c.get('doc_mode')}, score={c.get('score')}){link}"
        )

    note = f"*Doc RAG Agent*"
    backend = ""
    # surface backend lightly when present in citations path
    from agent.services import qdrant_store

    if qdrant_store.qdrant_configured():
        note += " _(Qdrant)_"
    note += f"\n\n{result.get('answer') or '_No answer_'}\n"
    if cite_lines:
        note += "\n*Sources*\n" + "\n".join(cite_lines)
    if result.get("vector_backend"):
        backend = str(result["vector_backend"])
        note += f"\n\n_vector: {backend}_"
    related = result.get("related_queries") or []
    if related:
        note += "\n\n*Related*\n" + "\n".join(f"• `{q}`" for q in related[:3])

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
        "kb_citations": cites,
        "kb_related_queries": related,
    }
