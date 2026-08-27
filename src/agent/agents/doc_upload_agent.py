"""Doc Upload Agent — Teams chat attachments → SharePoint → ingest → optional summary.

Feature: Document Knowledge Fabric. ENABLE_DOC_KNOWLEDGE default false.
"""

from __future__ import annotations

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.agent_availability import doc_knowledge_offline
from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
from agent.services import doc_upload, ms_graph

logger = get_logger(__name__)

AGENT_NAME = "doc_upload_agent"


async def upload_and_ingest_documents(state: AgentState) -> AgentState:
    if not settings.enable_doc_knowledge:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": doc_knowledge_offline(agent_label="Doc Upload Agent"),
        }

    if not ms_graph.graph_configured():
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Doc Upload Agent* — Graph credentials missing. "
                "See `docs/DOCUMENT_KNOWLEDGE_FABRIC.md`."
            ),
        }

    phone = state.get("whatsapp_phone") or ""
    attachments = list(state.get("attachments") or [])
    user_msg = state.get("user_message") or ""
    if not attachments and user_msg:
        attachments.extend(doc_upload.attachments_from_share_links(user_msg))
    if phone and not attachments:
        try:
            session = await get_session(phone)
            pending_raw = list((session.get("data") or {}).get("pending_attachments") or [])
            attachments = doc_upload.attachments_from_session(pending_raw)
        except Exception:
            logger.exception("Failed reading pending attachments")

    summarize = bool(state.get("upload_summarize"))
    focus = doc_upload.summarize_focus_from_message(state.get("user_message") or "")

    result = await doc_upload.upload_and_ingest_attachments(
        attachments,
        owner_session=phone or None,
        summarize=summarize,
        focus=focus,
    )

    if phone:
        try:
            await save_session(
                phone,
                awaiting=None,
                clear_awaiting=True,
                data={"pending_attachments": []},
                merge_data=True,
            )
        except Exception:
            logger.exception("Failed clearing pending attachments")

    phone = state.get("whatsapp_phone") or ""
    if phone and result.get("status") == "completed":
        try:
            from agent.services.workspace_handoff import WS_KNOWLEDGE, mark_workspace

            await mark_workspace(phone, WS_KNOWLEDGE)
        except Exception:
            logger.exception("Failed marking knowledge workspace after upload")

    return {
        **state,
        "status": result.get("status") or "failed",
        "handled_by": AGENT_NAME,
        "notification_text": result.get("message") or "*Doc Upload Agent* failed.",
        "kb_ingest_results": result.get("ingest_results") or [],
        "error": "" if result.get("status") == "completed" else "upload_ingest_failed",
    }
