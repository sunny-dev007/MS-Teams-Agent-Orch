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
    # FinOps / cost chat context (used by generic author; Orbit FinOps path uses full session)
    sub = session_data.get("azure_finops_sub") or {}
    if sub.get("name") or sub.get("id"):
        parts.append(
            f"Azure FinOps subscription in session: {sub.get('name') or sub.get('id')}"
        )
    for key, label in (
        ("azure_finops_last_costs_svc", "Recent cost-by-service rows"),
        ("azure_finops_last_costs_rg", "Recent cost-by-RG rows"),
        ("azure_finops_last_recs", "Recent FinOps recommendations"),
    ):
        rows = session_data.get(key) or []
        if isinstance(rows, list) and rows:
            parts.append(f"{label}: {str(rows[:8])[:1200]}")
    bubbles = session_data.get("agent_recent_bubbles") or []
    if isinstance(bubbles, list) and bubbles:
        recent = []
        for b in bubbles[-6:]:
            if isinstance(b, dict) and b.get("text"):
                recent.append(f"{b.get('role')}: {str(b.get('text'))[:240]}")
        if recent:
            parts.append("Recent chat bubbles:\n" + "\n".join(recent))
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
            session_data=session_data,
            channel_surface=str(session_data.get("channel_surface") or ""),
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

    report_line = ""
    if result.get("report_kind") == "orbit_finops_cost":
        charts = result.get("charts") or []
        chart_bit = f" · charts: {', '.join(charts)}" if charts else ""
        report_line = f"**Report:** Orbit FinOps cost pack{chart_bit}\n"

    return {
        **state,
        "status": "general_response",
        "handled_by": AGENT_NAME,
        "notification_text": (
            f"*Doc Author Agent* — document published to SharePoint.\n\n"
            f"**Title:** {result.get('title')}\n"
            f"**Folder:** `{result.get('folder')}`\n"
            f"{report_line}\n"
            f"{link_block}\n\n"
            "_Next:_ *list my documents* to see it in the library · "
            "*ingest N* to vectorize · *ask docs …* to query it after ingest."
        ),
        "doc_author_result": result,
    }
