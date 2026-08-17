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
    lines = [f"_Tags:_ {graph_docs.SOURCE_TAG_LEGEND}", ""]
    for row in catalog:
        src = graph_docs.source_tag(row.get("source_type"))
        mode = row.get("doc_mode") or "general"
        folder = row.get("folder") or ""
        loc = f" / {folder}" if folder else ""
        lines.append(
            f"{row.get('pick')}. [{src}] *{row.get('title')}* "
            f"({mode}{loc})"
        )
    return "\n".join(lines)


def _sources_footer() -> str:
    raw = (settings.doc_knowledge_sources or "sharepoint").lower()
    enabled = [p.strip() for p in raw.split(",") if p.strip()]
    bits = []
    for s, tag in (
        ("sharepoint", "SP"),
        ("onedrive", "OD"),
        ("onenote", "ON"),
    ):
        on = s in enabled
        if s == "onedrive" and on and not (settings.ms_graph_onedrive_user_id or "").strip():
            bits.append(f"{tag}_needs_user_id")
        elif on:
            bits.append(f"{tag}_on")
        else:
            bits.append(f"{tag}_off")
    return "_Sources:_ " + " · ".join(bits)


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
                f"{i}. [{graph_docs.source_tag(d['source_type'])}] *{d['title']}* "
                f"({d['doc_mode']}) chunks={d['chunk_count']} `{d['id']}`"
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
        f"{_sources_footer()}\n\n"
        "Reply *ingest 1,3* (or *ingest all*) to vectorize with metadata.\n"
        "Then *ask docs <question>* or *summarize docs <focus>*."
    )
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
        "session_awaiting": "doc_pick",
    }
