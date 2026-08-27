"""Doc Author Agent — LLM-generated Markdown + DOCX to SharePoint.

Distinct from Docs Agent (release notes HTML) and Doc Upload (Teams attachments).
"""

from __future__ import annotations

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.agent_availability import doc_knowledge_offline
from agent.core.logging import get_logger
from agent.services import document_author

logger = get_logger(__name__)

AGENT_NAME = "doc_author_agent"


def _conversation_context_from_session(session_data: dict) -> str:
    parts: list[str] = []
    if session_data.get("doc_query"):
        parts.append(f"Last doc question: {session_data['doc_query']}")
    if session_data.get("last_doc_answer"):
        parts.append(f"Last doc answer excerpt: {str(session_data['last_doc_answer'])[:500]}")
    if session_data.get("last_plan"):
        parts.append(f"Meeting plan context: {str(session_data['last_plan'].get('title', ''))[:200]}")
    return "\n".join(parts)


async def run_doc_author(state: AgentState) -> AgentState:
    if not settings.enable_doc_knowledge:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": doc_knowledge_offline(agent_label="Doc Author Agent"),
        }

    msg = state.get("user_message") or ""
    phone = state.get("whatsapp_phone") or ""
    session_data: dict = {}
    if phone:
        try:
            from agent.core.session import get_session

            session_data = (await get_session(phone)).get("data") or {}
        except Exception:
            logger.exception("doc_author failed reading session")

    try:
        result = await document_author.create_sharepoint_document(
            msg,
            conversation_context=_conversation_context_from_session(session_data),
        )
    except Exception as exc:
        logger.exception("Doc Author failed")
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                f"*Doc Author Agent* — could not create the document in SharePoint.\n\n"
                f"_{exc}_\n\n"
                "If you meant **release notes for a PR**, say *write release notes for PR &lt;n&gt;*.\n"
                "To upload an existing file, attach it in Teams or paste a SharePoint sharing link."
            ),
            "error": str(exc),
        }

    links = []
    if result.get("docx_url"):
        links.append(f"**DOCX:** {result['docx_url']}")
    if result.get("md_url"):
        links.append(f"**Markdown:** {result['md_url']}")
    link_block = "\n".join(links) if links else "_SharePoint links unavailable — check Graph permissions._"

    return {
        **state,
        "status": "general_response",
        "handled_by": AGENT_NAME,
        "notification_text": (
            f"*Doc Author Agent* — document published to SharePoint.\n\n"
            f"**Title:** {result.get('title')}\n"
            f"**Folder:** `{result.get('folder')}`\n\n"
            f"{link_block}\n\n"
            "_Next:_ *list my documents* to see it in the library · "
            "*ingest N* to vectorize · *ask docs …* to query it after ingest."
        ),
        "doc_author_result": result,
    }
