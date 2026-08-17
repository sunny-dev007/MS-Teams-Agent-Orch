"""Doc Library Agent — list SharePoint / OneDrive / OneNote for Teams selection.

Feature: Document Knowledge Fabric. ENABLE_DOC_KNOWLEDGE default false.
"""

from __future__ import annotations

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.core.session import save_session
from agent.models import knowledge_doc as kd
from agent.services import graph_docs, ms_graph

logger = get_logger(__name__)

AGENT_NAME = "doc_library_agent"

_DISABLED = (
    "*Doc Library Agent* is installed but **disabled** (ENABLE_DOC_KNOWLEDGE=false).\n\n"
    "When enabled: *list my documents* → pick numbers → *ingest 1,2* → "
    "*ask docs …* / *summarize docs …*.\n"
    "Dev Agent and WhatsApp coding paths are unchanged."
)


def _format_catalog(catalog: list[dict]) -> str:
    if not catalog:
        return "_No files found. Check Graph site settings / OneDrive user / Notes permissions._"
    lines = []
    for row in catalog:
        src = (row.get("source_type") or "?").upper()[:2]
        mode = row.get("doc_mode") or "general"
        folder = row.get("folder") or ""
        loc = f" / {folder}" if folder else ""
        lines.append(
            f"{row.get('pick')}. [{src}] *{row.get('title')}* "
            f"({mode}{loc})"
        )
    return "\n".join(lines)


async def list_documents(state: AgentState) -> AgentState:
    if not settings.enable_doc_knowledge:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": _DISABLED,
        }

    if not ms_graph.graph_configured():
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Doc Library Agent* — Graph credentials missing.\n"
                "See `docs/DOCUMENT_KNOWLEDGE_FABRIC.md`."
            ),
        }

    msg = (state.get("user_message") or "").lower()
    phone = state.get("whatsapp_phone") or ""

    # Inventory of already-ingested docs
    if "ingested" in msg or "knowledge base" in msg or "kb status" in msg:
        docs = await kd.list_ready_documents(owner_session=phone or None, limit=30)
        if not docs:
            # Also show global ready docs (shared KB on App Service)
            docs = await kd.list_ready_documents(limit=30)
        if not docs:
            note = (
                "*Doc Library* — no ingested documents yet.\n\n"
                "Say *list my documents* then *ingest 1,3*."
            )
        else:
            lines = [
                f"{i}. *{d['title']}* [{d['source_type']}/{d['doc_mode']}] "
                f"chunks={d['chunk_count']} `{d['id']}`"
                for i, d in enumerate(docs, start=1)
            ]
            note = "*Ingested knowledge base*\n\n" + "\n".join(lines)
            note += "\n\nAsk: *ask docs …* or *summarize docs …*"
        return {
            **state,
            "status": "completed",
            "handled_by": AGENT_NAME,
            "notification_text": note,
        }

    try:
        catalog = await graph_docs.list_knowledge_catalog()
    except Exception as exc:
        logger.exception("Doc library list failed")
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": f"*Doc Library Agent* failed listing: {exc}",
            "error": str(exc),
        }

    if phone:
        try:
            await save_session(
                phone,
                awaiting="doc_pick",
                data={"doc_catalog": catalog, "channel": "teams" if phone.startswith("teams:") else "whatsapp"},
                merge_data=True,
            )
        except Exception:
            logger.exception("Failed saving doc_catalog session")

    note = (
        "*Doc Library Agent* — selectable documents\n\n"
        f"{_format_catalog(catalog)}\n\n"
        "Reply *ingest 1,3* (or *ingest all*) to vectorize with metadata.\n"
        "Then *ask docs <question>* or *summarize docs <focus>*."
    )
    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "notification_text": note,
        "session_awaiting": "doc_pick",
    }
