"""Publish meeting plans to SharePoint (Markdown + optional DOCX)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import graph_docs

logger = get_logger(__name__)


def _slug(text: str, *, max_len: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:max_len] or "plan").strip("-")


def plan_filenames(plan: dict[str, Any]) -> tuple[str, str]:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slug(plan.get("title") or "implementation-plan")
    base = f"{date}-{slug}-plan"
    return f"{base}.md", f"{base}.docx"


def render_docx_bytes(plan: dict[str, Any], markdown: str) -> bytes | None:
    """Render DOCX when python-docx is available; otherwise None."""
    try:
        from docx import Document
        from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    except ImportError:
        logger.info("python-docx not installed — MD-only publish")
        return None

    doc = Document()
    title = plan.get("title") or "Implementation Plan"
    h = doc.add_heading(title, level=0)
    h.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT

    for line in markdown.splitlines():
        text = line.rstrip()
        if not text:
            doc.add_paragraph("")
            continue
        if text.startswith("# "):
            doc.add_heading(text[2:].strip(), level=1)
        elif text.startswith("## "):
            doc.add_heading(text[3:].strip(), level=2)
        elif text.startswith("### "):
            doc.add_heading(text[4:].strip(), level=3)
        elif text.startswith("| ") and "---" not in text:
            doc.add_paragraph(text.replace("|", " · "))
        elif text.startswith("- "):
            doc.add_paragraph(text[2:], style="List Bullet")
        else:
            doc.add_paragraph(text)

    import io

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


async def publish_plan(
    plan: dict[str, Any],
    markdown: str,
) -> dict[str, Any]:
    folder = (settings.meeting_plans_folder or "MeetingPlans").strip()
    md_name, docx_name = plan_filenames(plan)
    formats = (settings.meeting_plan_default_format or "md,docx").lower()

    md_url = ""
    docx_url = ""
    if "md" in formats:
        entry = await graph_docs.upload_site_drive_item(
            folder=folder,
            filename=md_name,
            content=markdown.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
        md_url = (entry or {}).get("web_url") or ""

    docx_bytes = render_docx_bytes(plan, markdown) if "docx" in formats else None
    if docx_bytes:
        entry = await graph_docs.upload_site_drive_item(
            folder=folder,
            filename=docx_name,
            content=docx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        docx_url = (entry or {}).get("web_url") or ""

    return {
        "title": plan.get("title") or "Implementation Plan",
        "md_url": md_url,
        "docx_url": docx_url,
        "md_filename": md_name,
        "docx_filename": docx_name,
        "folder": folder,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
