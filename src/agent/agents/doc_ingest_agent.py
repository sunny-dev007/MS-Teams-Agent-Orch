"""Doc Ingest Agent — vectorize selected SharePoint/OneDrive/OneNote docs.

Feature: Document Knowledge Fabric. ENABLE_DOC_KNOWLEDGE default false.
"""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
from agent.services import doc_knowledge, graph_docs, ms_graph

logger = get_logger(__name__)

AGENT_NAME = "doc_ingest_agent"

_NUMS_RE = re.compile(r"\b(\d+)\b")
_ALL_RE = re.compile(r"\bingest\s+all\b|\ball\s+documents?\b", re.I)


async def ingest_documents(state: AgentState) -> AgentState:
    if not settings.enable_doc_knowledge:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Doc Ingest Agent* is installed but **disabled** "
                "(ENABLE_DOC_KNOWLEDGE=false).\n"
                "Dev/WhatsApp coding paths are unchanged."
            ),
        }

    if not ms_graph.graph_configured():
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Doc Ingest Agent* — Graph credentials missing. "
                "See `docs/DOCUMENT_KNOWLEDGE_FABRIC.md`."
            ),
        }

    msg = state.get("user_message") or ""
    phone = state.get("whatsapp_phone") or ""
    catalog: list = []
    if phone:
        session = await get_session(phone)
        catalog = list((session.get("data") or {}).get("doc_catalog") or [])

    if not catalog:
        # Allow re-list then ingest in one turn is heavy; nudge user.
        try:
            catalog = await graph_docs.list_knowledge_catalog()
        except Exception as exc:
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": (
                    f"*Doc Ingest Agent* — no catalog in session and list failed: {exc}\n"
                    "Say *list my documents* first."
                ),
                "error": str(exc),
            }

    if _ALL_RE.search(msg):
        selected = catalog
    else:
        picks = {int(n) for n in _NUMS_RE.findall(msg)}
        selected = [row for row in catalog if int(row.get("pick") or 0) in picks]

    if not selected:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Doc Ingest Agent* — could not match selection.\n"
                "Say *list my documents*, then *ingest 1,2* or *ingest all*."
            ),
        }

    results = await doc_knowledge.ingest_catalog_entries(selected, owner_session=phone or None)
    ok = [r for r in results if r.get("status") == "ready"]
    bad = [r for r in results if r.get("status") != "ready"]

    lines = [
        f"✓ *{r['title']}* — {r.get('chunks', 0)} chunks "
        f"({r.get('extract_status')}, vec={r.get('vector_backend', 'sqlite')})"
        for r in ok
    ]
    lines += [f"✗ *{r.get('title')}* — {r.get('reason')}" for r in bad]

    note = (
        f"*Doc Ingest Agent* — vectorized {len(ok)}/{len(results)}\n\n"
        + "\n".join(lines)
        + "\n\nNext: *ask docs <your question>* or *summarize docs <focus>*\n"
        "Inventory: *list ingested documents*"
    )

    if phone:
        try:
            await save_session(phone, awaiting=None, clear_awaiting=True, merge_data=True)
        except Exception:
            logger.exception("Failed clearing doc_pick after ingest")

    return {
        **state,
        "status": "completed" if ok else "failed",
        "handled_by": AGENT_NAME,
        "notification_text": note,
        "kb_ingest_results": results,
    }
