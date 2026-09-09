"""Generate enterprise documents (Markdown + DOCX) and publish to SharePoint.

Feature: Document Knowledge Fabric extension — server-side authoring when Teams
attachments are unavailable. Distinct from release-notes HTML and file upload.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import graph_docs, ms_graph
from agent.services.llm import invoke_llm
from agent.services.meeting_publish import render_docx_bytes

logger = get_logger(__name__)

_STRIP_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"prepare|create|write|generate|draft|produce|save|publish"
    r")\s+(?:an?\s+)?(?:\w+\s+){0,4}?"
    r"(?:document|report|docx?|write-?up|brief|memo|summary\s+report)\s*"
    r"(?:about|on|for|regarding|titled)?\s*",
    re.I,
)

_SYSTEM = """You are an enterprise technical writer for Sunny's organization.
Given a user request, produce a professional document as Markdown only.

Structure:
- # Title (clear, specific)
- ## Executive summary (2-4 sentences)
- ## Background / context
- ## Main content (sections with ## / ### as needed)
- ## Recommendations or next steps (bullets)
- ## Appendix (optional tables)

Use tables where helpful. No HTML. No code fences around the whole document.
Be factual; if details are missing, state reasonable enterprise placeholders clearly."""


def extract_topic(message: str) -> str:
    raw = (message or "").strip()
    topic = _STRIP_PREFIX.sub("", raw).strip(" :.-")
    if not topic:
        topic = raw
    return topic[:2000]


def document_filenames(title: str) -> tuple[str, str]:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "document").lower()).strip("-")[:40]
    base = f"{stamp}-{slug or 'document'}"
    return f"{base}.md", f"{base}.docx"


async def generate_markdown(topic: str, *, conversation_context: str = "") -> tuple[str, str]:
    from agent.services.orbit_turn import is_orbit_surface

    # Orbit gets gpt-4.1 via doc_author role; Copilot Studio keeps shared default model.
    role = "doc_author" if is_orbit_surface() else "default"
    user_parts = [f"Document request:\n{topic}"]
    if conversation_context:
        user_parts.append(f"\nRecent conversation context (use if relevant):\n{conversation_context[:3000]}")
    response = await invoke_llm(
        [SystemMessage(content=_SYSTEM), HumanMessage(content="\n".join(user_parts))],
        temperature=0.3,
        role=role,
    )
    md = (response.content or "").strip()
    if not md:
        raise ValueError("LLM returned empty document content")
    title_match = re.search(r"^#\s+(.+)$", md, re.M)
    title = (title_match.group(1).strip() if title_match else topic[:80]) or "Document"
    return title, md


async def publish_document(
    title: str,
    markdown: str,
    *,
    folder: str | None = None,
) -> dict[str, Any]:
    target = (folder or settings.doc_author_folder or "Documents/Generated").strip().strip("/")
    md_name, docx_name = document_filenames(title)

    plan_stub = {"title": title, "executive_summary": ""}
    md_url = ""
    docx_url = ""

    md_entry = await graph_docs.upload_site_drive_item(
        folder=target,
        filename=md_name,
        content=markdown.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )
    md_url = (md_entry or {}).get("web_url") or ""

    docx_bytes = render_docx_bytes(plan_stub, markdown, plan_kind="action")
    if docx_bytes:
        docx_entry = await graph_docs.upload_site_drive_item(
            folder=target,
            filename=docx_name,
            content=docx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        docx_url = (docx_entry or {}).get("web_url") or ""

    return {
        "title": title,
        "folder": target,
        "md_url": md_url,
        "docx_url": docx_url,
        "md_filename": md_name,
        "docx_filename": docx_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def create_sharepoint_document(
    message: str,
    *,
    conversation_context: str = "",
    session_data: dict[str, Any] | None = None,
    channel_surface: str = "",
) -> dict[str, Any]:
    if not ms_graph.doc_knowledge_ready():
        raise RuntimeError(
            "Microsoft Graph / SharePoint is not configured for document publishing."
        )
    data = session_data or {}
    surface = (channel_surface or str(data.get("channel_surface") or "")).strip().lower()

    # Orbit-only: FinOps cost / optimisation DOCX with charts + live Cost Management.
    # Copilot Studio (AI Dev Agent) keeps the previous generic author path unchanged.
    if surface == "orbit":
        from agent.services import finops_document

        if finops_document.wants_finops_cost_report(message, session_data=data):
            return await _create_orbit_finops_document(message, session_data=data)

    topic = extract_topic(message)
    title, markdown = await generate_markdown(topic, conversation_context=conversation_context)
    return await publish_document(title, markdown)


async def _create_orbit_finops_document(
    message: str,
    *,
    session_data: dict[str, Any],
) -> dict[str, Any]:
    """Orbit FinOps report — isolated path; never used by Copilot Studio."""
    from agent.services import finops_document

    if not settings.enable_azure_finops_agent:
        raise RuntimeError(
            "Azure FinOps Agent is disabled. Enable ENABLE_AZURE_FINOPS_AGENT to "
            "author cost-optimisation documents from live spend data."
        )
    if not azure_finops_ready_safe():
        raise RuntimeError(
            "Azure ARM / Cost Management credentials are not configured for FinOps reports."
        )

    bundle = await finops_document.gather_finops_bundle(session_data=session_data)
    narrative = await finops_document.polish_narrative(bundle, message)
    markdown = finops_document.build_markdown(bundle, narrative=narrative)
    charts = finops_document.render_charts(bundle)
    title = "Cost Optimisation Recommendations"

    target = (settings.doc_author_folder or "Documents/Generated").strip().strip("/")
    md_name, docx_name = document_filenames(title)

    md_entry = await graph_docs.upload_site_drive_item(
        folder=target,
        filename=md_name,
        content=markdown.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )
    md_url = (md_entry or {}).get("web_url") or ""

    docx_bytes = finops_document.render_finops_docx(bundle, markdown, charts)
    if not docx_bytes:
        docx_bytes = render_docx_bytes(
            {"title": title, "executive_summary": narrative[:400]},
            markdown,
            plan_kind="action",
        )
    docx_url = ""
    if docx_bytes:
        docx_entry = await graph_docs.upload_site_drive_item(
            folder=target,
            filename=docx_name,
            content=docx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        docx_url = (docx_entry or {}).get("web_url") or ""

    return {
        "title": title,
        "folder": target,
        "md_url": md_url,
        "docx_url": docx_url,
        "md_filename": md_name,
        "docx_filename": docx_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report_kind": "orbit_finops_cost",
        "charts": sorted(charts.keys()),
        "currency": bundle.get("currency"),
        "total_cost": bundle.get("total"),
    }


def azure_finops_ready_safe() -> bool:
    try:
        from agent.services.azure_finops import azure_finops_ready

        return bool(azure_finops_ready())
    except Exception:
        return False
