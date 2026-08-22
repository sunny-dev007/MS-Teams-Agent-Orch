"""Teams chat attachment → SharePoint upload → Doc Ingest orchestration.

Feature: Document Knowledge Fabric (ENABLE_DOC_KNOWLEDGE). Additive only.
"""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import doc_extract, doc_knowledge, graph_docs, ms_graph

logger = get_logger(__name__)

SUPPORTED_INGEST_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".csv",
    ".md",
    ".markdown",
    ".txt",
)
SUPPORTED_INGEST_FORMATS_LABEL = "PDF, DOCX, PPTX, XLSX, CSV, MD, TXT"

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}

_SUMMARIZE_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(?:summar(?:ize|ise)|doc\s+insights?|insights?\s+(?:on|from))\s+"
    r"(?:this|the|attached|uploaded)?\s*(?:file|pdf|document|doc)?\s*[:\-]?\s*",
    re.I,
)
_INGEST_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(?:ingest|index|vectorize|upload|add\s+to\s+(?:the\s+)?(?:knowledge|kb)|"
    r"process|save\s+to\s+sharepoint)\s+"
    r"(?:this|the|attached|uploaded)?\s*(?:file|pdf|document|doc)?\s*(?:into\s+(?:the\s+)?system)?\s*[:\-]?\s*",
    re.I,
)
# Explicit upload/ingest verbs — must not match casual phrases like "here is the file".
_UPLOAD_ACTION_RE = re.compile(
    r"(?:^|\b)(?:please\s+)?(?:"
    r"ingest|index|vectorize|"
    r"add\s+to\s+(?:the\s+)?(?:knowledge|kb)|"
    r"upload(?:\s+(?:this|to\s+sharepoint|and\s+ingest))?|"
    r"save(?:\s+to\s+sharepoint)?|"
    r"process\s+(?:this|the)\s+(?:file|document|pdf|doc)|"
    r"ingest\s+into\s+(?:the\s+)?system"
    r")(?:\s|$)|"
    r"\b(?:attached|uploaded)\s+(?:file|pdf|document|docx|pptx|spreadsheet)\b|"
    r"\bthis\s+(?:attached\s+)?(?:file|pdf|document|docx|pptx|spreadsheet)\b|"
    r"\bput\s+(?:this|it)\s+in\s+(?:the\s+)?(?:library|knowledge|sharepoint)\b",
    re.I,
)


def normalize_attachment(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize Copilot/Teams attachment payload to {filename, content, content_type}."""
    if raw.get("content") and isinstance(raw.get("content"), (bytes, bytearray)):
        filename = (raw.get("filename") or raw.get("name") or "upload.bin").strip()
        content_type = (raw.get("content_type") or raw.get("mime_type") or "").strip()
        ext = ""
        if "." in filename:
            ext = "." + filename.rsplit(".", 1)[-1].lower()
        if not content_type and ext:
            content_type = _MIME_BY_EXT.get(ext, "application/octet-stream")
        return {
            "filename": filename,
            "content": bytes(raw["content"]),
            "content_url": "",
            "content_type": content_type,
            "extension": ext,
        }

    filename = (
        raw.get("filename")
        or raw.get("name")
        or raw.get("Name")
        or raw.get("fileName")
        or "upload.bin"
    ).strip()
    content_type = (
        raw.get("content_type")
        or raw.get("mime_type")
        or raw.get("contentType")
        or raw.get("MediaType")
        or raw.get("mediaType")
        or ""
    ).strip()
    content_b64 = (
        raw.get("content_base64")
        or raw.get("contentBase64")
        or raw.get("content_bytes")
        or raw.get("contentBytes")
        or raw.get("ContentBytes")
        or ""
    )
    content_url = raw.get("content_url") or raw.get("contentUrl") or raw.get("url") or ""

    if content_b64:
        try:
            content = base64.b64decode(content_b64, validate=False)
        except Exception:
            logger.warning("Invalid base64 attachment filename=%s", filename)
            return None
    elif content_url:
        content = None  # fetched later
    else:
        return None

    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    if not content_type and ext:
        content_type = _MIME_BY_EXT.get(ext, "application/octet-stream")

    return {
        "filename": filename,
        "content": content,
        "content_url": content_url,
        "content_type": content_type,
        "extension": ext,
    }


def attachments_for_session(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """JSON-safe attachment rows for conversation_sessions.data_json (no raw bytes)."""
    out: list[dict[str, Any]] = []
    for raw in attachments or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        content = row.pop("content", None)
        if isinstance(content, (bytes, bytearray)):
            row["content_base64"] = base64.b64encode(bytes(content)).decode("ascii")
        elif content is not None and not row.get("content_base64"):
            row.pop("content", None)
        out.append(row)
    return out


def attachments_from_session(stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Restore in-memory attachments (bytes) from session JSON rows."""
    out: list[dict[str, Any]] = []
    for raw in stored or []:
        if not isinstance(raw, dict):
            continue
        if isinstance(raw.get("content"), (bytes, bytearray)):
            out.append(dict(raw))
            continue
        norm = normalize_attachment(raw)
        if norm:
            out.append(norm)
    return out


def wants_upload_action(message: str) -> bool:
    """True when the user explicitly asks to upload/ingest an attached Teams file."""
    msg = (message or "").strip()
    if not msg:
        return False
    # Catalog picks like ingest 1,3 / ingest all — not Teams attachment upload.
    if re.search(r"\bingest\s+(?:\d+|all|stale|documents?)\b", msg, re.I):
        return False
    return bool(_UPLOAD_ACTION_RE.search(msg))


def message_expects_teams_attachment(message: str) -> bool:
    """True when the user refers to an attached/uploaded file (not corpus summarize docs)."""
    msg = (message or "").strip()
    if not msg:
        return False
    if wants_upload_action(msg):
        return True
    # Corpus insights — summarize docs / doc insights (no attachment path).
    if re.search(
        r"(?:summarize\s+docs?|summarise\s+docs?|doc\s+insights?|"
        r"insights?\s+(?:on|from)\s+docs?|document\s+insights?)",
        msg,
        re.I,
    ):
        return False
    if re.search(r"\bsummar(?:ize|ise)\b", msg, re.I):
        if re.search(
            r"\b(?:this|it|attached|uploaded|into\s+the\s+system)\b",
            msg,
            re.I,
        ):
            return True
        if re.search(r"\b(?:pdf|file|docx|pptx|spreadsheet|xlsx|csv)\b", msg, re.I):
            return True
    if re.search(r"\bingest\b", msg, re.I) and re.search(
        r"\b(?:this|attached|uploaded|into\s+(?:the\s+)?system|the\s+(?:pdf|file))\b",
        msg,
        re.I,
    ):
        return True
    return False


def attachment_not_received_reply() -> str:
    """Honest failure when Teams/Copilot did not pass attachment bytes to the API."""
    return (
        "*Doc Upload Agent* — your message asks to ingest/summarize an **attached file**, "
        "but **no file bytes** arrived from Teams.\n\n"
        "The Copilot **PersonalAIAgent** action must map `attachments[]` with "
        "`filename` + `content_base64` (connector swagger **v1.0.7**). "
        "Text-only calls cannot upload to SharePoint.\n\n"
        "**Manual workaround:** upload to SharePoint → *list my documents* → *ingest N*.\n\n"
        f"**Supported ingestion formats:** {SUPPORTED_INGEST_FORMATS_LABEL}."
    )


def unsupported_format_message(filename: str, ext: str = "", mime: str = "") -> str:
    shown = ext or mime or "unknown"
    return (
        f"*{filename}* — format `{shown}` is **not supported** for ingestion.\n"
        f"Supported: {SUPPORTED_INGEST_FORMATS_LABEL}."
    )


async def _fetch_url_bytes(url: str, *, max_bytes: int) -> bytes:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
        if len(data) > max_bytes:
            raise ValueError(f"Attachment exceeds {max_bytes // (1024 * 1024)} MB limit")
        return data


def validate_attachment(att: dict[str, Any]) -> tuple[bool, str]:
    filename = att.get("filename") or "upload.bin"
    ext = att.get("extension") or ""
    if not ext and "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    mime = att.get("content_type") or ""
    if not doc_extract.is_supported_extension(ext, mime):
        return False, unsupported_format_message(filename, ext=ext, mime=mime)
    content = att.get("content")
    if content is None:
        if att.get("content_url"):
            return True, ""
        return False, "Attachment has no content."
    max_bytes = max(1, int(settings.doc_upload_max_bytes or 25 * 1024 * 1024))
    if len(content) > max_bytes:
        return False, f"`{filename}` exceeds the {max_bytes // (1024 * 1024)} MB upload limit."
    if len(content) < 8:
        return False, f"`{filename}` looks empty."
    return True, ""


def summarize_focus_from_message(message: str) -> str:
    raw = (message or "").strip()
    focus = _SUMMARIZE_PREFIX.sub("", raw).strip()
    focus = _INGEST_PREFIX.sub("", focus).strip()
    return focus or "overall themes and risks"


async def upload_and_ingest_attachments(
    attachments: list[dict[str, Any]],
    *,
    owner_session: str | None,
    summarize: bool = False,
    focus: str = "",
) -> dict[str, Any]:
    """Upload attachments to SharePoint, ingest into KB, optionally summarize."""
    if not settings.enable_doc_knowledge:
        return {
            "status": "skipped",
            "message": "*Doc Upload Agent* is disabled (ENABLE_DOC_KNOWLEDGE=false).",
        }
    if not ms_graph.graph_configured():
        return {
            "status": "failed",
            "message": "*Doc Upload Agent* — Microsoft Graph is not configured.",
        }
    if not attachments:
        return {
            "status": "failed",
            "message": attachment_not_received_reply(),
        }

    folder = (settings.doc_upload_folder or "UploadedDocs").strip().strip("/") or "UploadedDocs"
    max_bytes = max(1, int(settings.doc_upload_max_bytes or 25 * 1024 * 1024))
    uploaded: list[dict[str, Any]] = []
    errors: list[str] = []

    for raw in attachments:
        att = raw if isinstance(raw.get("content"), (bytes, bytearray)) and raw.get("filename") else normalize_attachment(raw)
        if not att:
            errors.append("Could not read one attachment (missing content).")
            continue
        if att.get("content") is None and att.get("content_url"):
            try:
                att["content"] = await _fetch_url_bytes(att["content_url"], max_bytes=max_bytes)
            except Exception as exc:
                errors.append(f"Download failed for `{att.get('filename')}`: {exc}")
                continue

        ok, reason = validate_attachment(att)
        if not ok:
            errors.append(reason)
            continue

        try:
            entry = await graph_docs.upload_site_drive_item(
                folder=folder,
                filename=att["filename"],
                content=att["content"],
                content_type=att.get("content_type") or "application/octet-stream",
            )
        except Exception as exc:
            logger.exception("SharePoint upload failed for %s", att.get("filename"))
            errors.append(f"SharePoint upload failed for `{att.get('filename')}`: {exc}")
            continue

        if not entry:
            errors.append(f"SharePoint upload failed for `{att.get('filename')}` (Graph error).")
            continue
        uploaded.append(entry)

    if not uploaded:
        msg = "*Doc Upload Agent* — nothing uploaded.\n\n" + "\n".join(f"• {e}" for e in errors)
        return {"status": "failed", "message": msg, "errors": errors}

    ingest_results = await doc_knowledge.ingest_catalog_entries(
        uploaded, owner_session=owner_session or None, skip_fresh=False
    )
    ok = [r for r in ingest_results if r.get("status") == "ready"]
    bad = [r for r in ingest_results if r.get("status") not in ("ready", "skipped")]

    lines = [
        f"✓ *{r['title']}* — uploaded to SharePoint/{folder} · "
        f"{r.get('ingest_action') or 'ingested'} · {r.get('chunks', 0)} chunks"
        for r in ok
    ]
    lines += [f"✗ *{r.get('title')}* — {r.get('reason')}" for r in bad]
    lines += [f"⚠ {e}" for e in errors]

    summary_block = ""
    docs_used: list[dict[str, Any]] = []
    if summarize and ok:
        doc_ids = [str(r.get("id") or "") for r in ok if r.get("id")]
        focus_text = (focus or "").strip() or "overall themes and risks"
        try:
            insight = await doc_knowledge.summarize_insights(
                focus_text,
                owner_session=owner_session,
                doc_ids=doc_ids or None,
            )
            summary_block = insight.get("summary") or ""
            docs_used = insight.get("docs_used") or []
        except Exception as exc:
            logger.exception("Post-upload summarize failed")
            summary_block = f"_Summarization failed: {exc}_"

    note = (
        f"*Doc Upload Agent* — processed **{len(ok)}/{len(uploaded)}** file(s)\n\n"
        + "\n".join(lines)
        + "\n\n_Files land in SharePoint → "
        f"`{folder}` — then *list my documents* shows them with tag **SP**._\n"
        "Next: *ask docs …* or *summarize docs …*"
    )
    if summary_block:
        used_line = ", ".join(d.get("title") or "" for d in docs_used[:5]) or ok[0].get("title", "")
        note += f"\n\n*Executive summary* ({used_line})\n\n{summary_block}"

    status = "completed" if ok else "failed"
    return {
        "status": status,
        "message": note,
        "uploaded": uploaded,
        "ingest_results": ingest_results,
        "errors": errors,
    }
