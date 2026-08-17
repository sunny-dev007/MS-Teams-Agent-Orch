"""Graph document listing + content fetch for Document Knowledge Fabric.

Lists SharePoint site drive, optional OneDrive (user), and OneNote pages.
Binary Office/PDF content is extracted via agent.services.doc_extract
(DOCX/PPTX/XLSX/PDF/CSV/MD) — never UTF-8-decode ZIP bytes into Qdrant.
Does not mutate live Docs/QA upload paths.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import doc_extract, ms_graph

logger = get_logger(__name__)

SOURCE_SHAREPOINT = "sharepoint"
SOURCE_ONEDRIVE = "onedrive"
SOURCE_ONENOTE = "onenote"

_TEXT_EXTS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".yml",
    ".yaml",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".cs",
    ".java",
}
_OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".pdf", ".doc", ".ppt", ".xls"}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip and data and data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._chunks)


def html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html or "")
    except Exception:
        return re.sub(r"<[^>]+>", " ", html or "")
    return parser.text()


def infer_doc_mode(name: str, mime: str = "") -> str:
    """Lightweight document-mode taxonomy for metadata / filtering."""
    lower = f"{name} {mime}".lower()
    if any(k in lower for k in ("policy", "compliance", "security", "sop")):
        return "policy"
    if any(k in lower for k in ("how-to", "howto", "guide", "runbook", "playbook")):
        return "howto"
    if any(k in lower for k in ("release", "changelog", "notes", "releasenotes")):
        return "release"
    if "onenote" in lower or lower.endswith(".one"):
        return "notes"
    if any(k in lower for k in ("prd", "spec", "design", "architecture")):
        return "spec"
    return "general"


async def resolve_site_id() -> tuple[str, str]:
    """Return (site_id, web_url) — shared with Docs Agent settings."""
    site_id = (settings.ms_graph_sharepoint_site_id or "").strip()
    if site_id:
        site = await ms_graph.graph_request("GET", f"/sites/{site_id}")
        return site["id"], site.get("webUrl") or ""

    host = (settings.ms_graph_sharepoint_hostname or "").strip()
    path = (settings.ms_graph_sharepoint_site_path or "").strip()
    if not host or not path:
        raise RuntimeError(
            "Set MS_GRAPH_SHAREPOINT_SITE_ID or "
            "MS_GRAPH_SHAREPOINT_HOSTNAME + MS_GRAPH_SHAREPOINT_SITE_PATH"
        )
    if not path.startswith("/"):
        path = "/" + path
    site = await ms_graph.graph_request("GET", f"/sites/{host}:{path}")
    return site["id"], site.get("webUrl") or ""


def _enabled_sources() -> set[str]:
    raw = (settings.doc_knowledge_sources or "sharepoint").lower()
    return {p.strip() for p in raw.split(",") if p.strip()}


def _drive_item_to_catalog(item: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    if item.get("folder") is not None and not item.get("file"):
        return None  # skip folders in v1 list (files only)
    name = item.get("name") or "untitled"
    mime = ((item.get("file") or {}).get("mimeType")) or ""
    ext = ""
    if "." in name:
        ext = "." + name.rsplit(".", 1)[-1].lower()
    return {
        "external_id": item.get("id") or "",
        "source_type": source,
        "title": name,
        "web_url": item.get("webUrl") or "",
        "mime_type": mime,
        "extension": ext,
        "size": int(item.get("size") or 0),
        "last_modified": item.get("lastModifiedDateTime") or "",
        "doc_mode": infer_doc_mode(name, mime),
        "download_path": f"/drives/{item.get('parentReference', {}).get('driveId', '')}/items/{item.get('id')}/content"
        if (item.get("parentReference") or {}).get("driveId")
        else "",
        "item_id": item.get("id") or "",
        "drive_id": (item.get("parentReference") or {}).get("driveId") or "",
    }


async def list_sharepoint_files(*, site_id: str, limit: int) -> list[dict[str, Any]]:
    items = await ms_graph.graph_paginate(
        f"/sites/{site_id}/drive/root/children",
        params={
            "$top": min(limit, 50),
            "$select": "id,name,webUrl,size,file,folder,lastModifiedDateTime,parentReference",
        },
        max_pages=2,
    )
    out: list[dict[str, Any]] = []
    for it in items:
        if it.get("folder") is not None and not it.get("file"):
            folder_id = it.get("id")
            if not folder_id:
                continue
            try:
                children = await ms_graph.graph_paginate(
                    f"/sites/{site_id}/drive/items/{folder_id}/children",
                    params={
                        "$top": min(limit, 50),
                        "$select": "id,name,webUrl,size,file,folder,lastModifiedDateTime,parentReference",
                    },
                    max_pages=1,
                )
            except Exception:
                logger.exception("Failed listing folder %s", it.get("name"))
                continue
            for child in children:
                row = _drive_item_to_catalog(child, source=SOURCE_SHAREPOINT)
                if row:
                    row["folder"] = it.get("name") or ""
                    out.append(row)
            continue
        row = _drive_item_to_catalog(it, source=SOURCE_SHAREPOINT)
        if row:
            row["folder"] = ""
            out.append(row)
        if len(out) >= limit:
            break
    return out[:limit]


async def list_onedrive_files(*, limit: int) -> list[dict[str, Any]]:
    user = (settings.ms_graph_onedrive_user_id or "").strip()
    if not user:
        return []
    items = await ms_graph.graph_paginate(
        f"/users/{user}/drive/root/children",
        params={
            "$top": min(limit, 50),
            "$select": "id,name,webUrl,size,file,folder,lastModifiedDateTime,parentReference",
        },
        max_pages=2,
    )
    out: list[dict[str, Any]] = []
    for it in items:
        row = _drive_item_to_catalog(it, source=SOURCE_ONEDRIVE)
        if row:
            row["folder"] = ""
            out.append(row)
        if len(out) >= limit:
            break
    return out


async def list_onenote_pages(*, site_id: str, limit: int) -> list[dict[str, Any]]:
    try:
        pages = await ms_graph.graph_paginate(
            f"/sites/{site_id}/onenote/pages",
            params={
                "$top": min(limit, 50),
                "$select": "id,title,createdDateTime,lastModifiedDateTime,links,contentUrl",
                "$orderby": "lastModifiedDateTime desc",
            },
            max_pages=1,
        )
    except Exception:
        logger.exception("OneNote list failed (needs Notes.Read.All / Sites.Read.All)")
        return []
    out: list[dict[str, Any]] = []
    for p in pages:
        title = p.get("title") or "Untitled page"
        links = p.get("links") or {}
        web = ((links.get("oneNoteWebUrl") or {}).get("href")) or ""
        out.append(
            {
                "external_id": p.get("id") or "",
                "source_type": SOURCE_ONENOTE,
                "title": title,
                "web_url": web,
                "mime_type": "text/html",
                "extension": ".one",
                "size": 0,
                "last_modified": p.get("lastModifiedDateTime") or "",
                "doc_mode": infer_doc_mode(title, "onenote"),
                "content_url": p.get("contentUrl") or "",
                "folder": "OneNote",
                "item_id": p.get("id") or "",
                "drive_id": "",
            }
        )
        if len(out) >= limit:
            break
    return out


async def list_knowledge_catalog(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Unified catalog for Teams selection (numbered list)."""
    if not ms_graph.graph_configured():
        raise RuntimeError("Microsoft Graph credentials not configured")
    cap = int(limit or settings.doc_knowledge_max_list or 25)
    sources = _enabled_sources()
    site_id = ""
    catalog: list[dict[str, Any]] = []

    need_site = SOURCE_SHAREPOINT in sources or SOURCE_ONENOTE in sources
    if need_site:
        site_id, _ = await resolve_site_id()

    per = max(5, cap // max(1, len(sources)))
    if SOURCE_SHAREPOINT in sources and site_id:
        catalog.extend(await list_sharepoint_files(site_id=site_id, limit=per))
    if SOURCE_ONEDRIVE in sources:
        catalog.extend(await list_onedrive_files(limit=per))
    if SOURCE_ONENOTE in sources and site_id:
        catalog.extend(await list_onenote_pages(site_id=site_id, limit=per))

    catalog.sort(key=lambda r: (r.get("source_type") or "", (r.get("title") or "").lower()))
    for i, row in enumerate(catalog[:cap], start=1):
        row["pick"] = i
    return catalog[:cap]


def _is_downloadable(entry: dict[str, Any]) -> bool:
    if entry.get("source_type") == SOURCE_ONENOTE:
        return False
    return True


async def fetch_document_text(entry: dict[str, Any]) -> tuple[str, str]:
    """Download + extract text. Returns (text, extract_status).

    Never UTF-8-decodes Office/PDF ZIP/binary into the vector store.
    """
    source = entry.get("source_type")
    title = entry.get("title") or "untitled"
    mime = entry.get("mime_type") or ""
    ext = entry.get("extension") or ""

    if source == SOURCE_ONENOTE:
        content_url = entry.get("content_url") or ""
        if not content_url:
            page_id = entry.get("item_id") or entry.get("external_id")
            if not page_id:
                return "", "missing_onenote_id"
            content_url = f"/sites/{(await resolve_site_id())[0]}/onenote/pages/{page_id}/content"
        raw, _ct = await ms_graph.graph_request_bytes(
            "GET", content_url, accept="text/html"
        )
        return html_to_text(raw.decode("utf-8", errors="replace")), "html_extracted"

    if not _is_downloadable(entry):
        return "", "not_downloadable"

    drive_id = entry.get("drive_id") or ""
    item_id = entry.get("item_id") or entry.get("external_id") or ""
    if drive_id and item_id:
        path = f"/drives/{drive_id}/items/{item_id}/content"
    elif item_id:
        site_id, _ = await resolve_site_id()
        path = f"/sites/{site_id}/drive/items/{item_id}/content"
    else:
        return "", "missing_item_id"

    raw, ct = await ms_graph.graph_request_bytes("GET", path)
    filename = title
    if ext and not title.lower().endswith(ext.lower()):
        filename = f"{title}{ext}"
    text, status = doc_extract.extract_bytes(
        raw,
        filename=filename,
        mime=ct or mime,
    )
    return (text or "").strip(), status
