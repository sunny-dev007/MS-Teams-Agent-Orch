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
from urllib.parse import quote

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import doc_extract, ms_graph

logger = get_logger(__name__)

SOURCE_SHAREPOINT = "sharepoint"
SOURCE_ONEDRIVE = "onedrive"
SOURCE_ONENOTE = "onenote"

# Short tags for Teams/WhatsApp lists (enterprise-readable)
SOURCE_TAGS = {
    SOURCE_SHAREPOINT: "SP",
    SOURCE_ONEDRIVE: "OD",
    SOURCE_ONENOTE: "ON",
}
SOURCE_TAG_LEGEND = "[SP]=SharePoint · [OD]=OneDrive · [ON]=OneNote"


def source_tag(source_type: str | None) -> str:
    """Return SP / OD / ON for a Graph source_type (unknown → ??)."""
    key = (source_type or "").strip().lower()
    return SOURCE_TAGS.get(key, "??")

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


def format_file_size(n: int | None) -> str:
    """Human-readable size for Teams/WhatsApp lists."""
    try:
        size = int(n or 0)
    except (TypeError, ValueError):
        return "—"
    if size <= 0:
        return "—"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def folder_from_parent(parent: dict[str, Any] | None) -> str:
    """Best-effort folder path from Graph parentReference.path."""
    if not parent:
        return ""
    raw = str(parent.get("path") or "")
    # e.g. /drives/{id}/root:/ReleaseNotes/sub
    if "/root:" in raw:
        rest = raw.split("/root:", 1)[-1].strip("/")
        return rest
    name = (parent.get("name") or "").strip()
    if name.lower() in ("root", "documents", ""):
        return ""
    return name


SEARCH_STOPWORDS = frozenset(
    {
        "list",
        "show",
        "get",
        "fetch",
        "display",
        "give",
        "pull",
        "browse",
        "find",
        "search",
        "locate",
        "look",
        "up",
        "of",
        "my",
        "the",
        "all",
        "me",
        "please",
        "from",
        "in",
        "on",
        "named",
        "called",
        "like",
        "about",
        "with",
        "for",
        "documents",
        "document",
        "docs",
        "doc",
        "files",
        "file",
        "sharepoint",
        "onedrive",
        "onenote",
        "library",
        "which",
        "as",
        "pdf",
        "pptx",
        "docx",
        "xlsx",
        "csv",
        "txt",
        "md",
        "a",
        "an",
        "is",
        "to",
        "into",
    }
)


def tokenize_search_keywords(text: str) -> list[str]:
    """Split filename-like text into keyword tokens (includes hyphen segments)."""
    raw = (text or "").lower()
    parts = re.split(r"[^a-z0-9._\-]+", raw)
    expanded: list[str] = []
    for part in parts:
        token = part.strip("._-")
        if len(token) < 2 or token in SEARCH_STOPWORDS:
            continue
        expanded.append(token)
        if "-" in token:
            for piece in token.split("-"):
                piece = piece.strip()
                if len(piece) >= 3 and piece not in SEARCH_STOPWORDS:
                    expanded.append(piece)
    seen: set[str] = set()
    out: list[str] = []
    for token in expanded:
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out[:8]


def parse_search_keywords(user_message: str) -> tuple[str, list[str]]:
    """Return (display query for UI, keyword tokens for AND matching)."""
    raw = (user_message or "").strip()
    if not raw:
        return "", []
    lower = raw.lower()
    if re.match(
        r"^\s*(next(?:\s+page)?|more(?:\s+documents?)?|previous|prev(?:ious)?\s*page|"
        r"page\s+\d+)\s*$",
        lower,
    ):
        return "", []

    named = re.search(
        r"(?:named|called)\s+(?:as\s+)?[:=\-]?\s*['\"]?([^\s'\"?]+)",
        raw,
        re.I,
    )
    if named:
        phrase = named.group(1).strip("'\"")
        kws = tokenize_search_keywords(phrase)
        display = phrase[:80] if phrase else " · ".join(kws)
        return display, kws

    quoted = re.search(r"['\"]([^'\"]{2,120})['\"]", raw)
    if quoted:
        phrase = quoted.group(1)
        kws = tokenize_search_keywords(phrase)
        return phrase[:80], kws

    cleaned = re.sub(
        r"\b(list|show|get|fetch|display|give|pull|browse|find|search|locate|"
        r"look\s+up|of|my|the|all|me|please|from|in|on|named|called|like|about|"
        r"with|for|documents?|docs|files?|sharepoint|onedrive|onenote|"
        r"document\s+library|library|which|as|pdf|pptx|docx|xlsx|csv|txt|md)\b",
        " ",
        raw,
        flags=re.I,
    )
    cleaned = re.sub(r"[?!.,]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 2:
        return "", []
    if len(cleaned) > 120:
        cleaned = cleaned[:120]
    kws = tokenize_search_keywords(cleaned)
    display = " · ".join(kws) if len(kws) > 1 else (kws[0] if kws else cleaned[:80])
    return display, kws


def row_matches_keywords(row: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return True
    hay = " ".join(
        [
            (row.get("title") or "").lower(),
            (row.get("folder") or "").lower(),
            (row.get("site_name") or "").lower(),
        ]
    )
    return all(kw in hay for kw in keywords)


def keyword_match_score(row: dict[str, Any], keywords: list[str]) -> int:
    title = (row.get("title") or "").lower()
    score = sum(2 for kw in keywords if kw in title)
    hay = " ".join(
        [
            title,
            (row.get("folder") or "").lower(),
            (row.get("site_name") or "").lower(),
        ]
    )
    score += sum(1 for kw in keywords if kw in hay)
    return score


def extract_library_query(user_message: str) -> str:
    """Filename/keyword for library search. Empty = browse all (paginated)."""
    display, _ = parse_search_keywords(user_message)
    return display


def parse_library_page(user_message: str, current: int, page_count: int) -> int:
    """Resolve next/prev/page N into a 0-based page index."""
    msg = (user_message or "").strip().lower()
    m = re.match(r"page\s+(\d+)", msg)
    if m:
        return max(0, min(int(m.group(1)) - 1, max(0, page_count - 1)))
    if msg.startswith("prev") or msg.startswith("previous"):
        return max(0, current - 1)
    if msg.startswith("next") or msg.startswith("more"):
        return min(current + 1, max(0, page_count - 1))
    return current


def _drive_item_to_catalog(
    item: dict[str, Any],
    *,
    source: str,
    site_name: str = "",
    site_web_url: str = "",
    folder: str = "",
) -> dict[str, Any] | None:
    if item.get("folder") is not None and not item.get("file"):
        return None  # skip folders in v1 list (files only)
    name = item.get("name") or "untitled"
    mime = ((item.get("file") or {}).get("mimeType")) or ""
    ext = ""
    if "." in name:
        ext = "." + name.rsplit(".", 1)[-1].lower()
    parent = item.get("parentReference") or {}
    folder_name = folder or folder_from_parent(parent)
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
        "download_path": f"/drives/{parent.get('driveId', '')}/items/{item.get('id')}/content"
        if parent.get("driveId")
        else "",
        "item_id": item.get("id") or "",
        "drive_id": parent.get("driveId") or "",
        "folder": folder_name,
        "site_name": site_name,
        "site_web_url": site_web_url,
    }


async def _site_meta(site_id: str) -> tuple[str, str, str]:
    """Return (id, displayName, webUrl)."""
    site = await ms_graph.graph_request(
        "GET", f"/sites/{site_id}?$select=id,displayName,webUrl,name"
    )
    name = (site.get("displayName") or site.get("name") or "SharePoint").strip()
    return str(site.get("id") or site_id), name, site.get("webUrl") or ""


async def list_accessible_sites(*, max_sites: int) -> list[dict[str, str]]:
    """Configured site first, then other Graph-visible site collections (capped)."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(sid: str, name: str, url: str) -> None:
        key = (sid or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        out.append({"id": key, "name": name or "SharePoint", "web_url": url or ""})

    try:
        sid, url = await resolve_site_id()
        sid, name, url = await _site_meta(sid)
        _add(sid, name, url)
    except Exception:
        logger.exception("Configured SharePoint site could not be resolved")

    if not settings.doc_knowledge_all_sites:
        return out[:max_sites]

    try:
        host = (settings.ms_graph_sharepoint_hostname or "").strip()
        q = host.split(".")[0] if host else "*"
        body = await ms_graph.graph_request(
            "GET",
            f"/sites?search={quote(q)}&$select=id,displayName,webUrl,name&$top={max(max_sites, 10)}",
        )
        for s in body.get("value") or []:
            if not isinstance(s, dict):
                continue
            _add(
                str(s.get("id") or ""),
                (s.get("displayName") or s.get("name") or "").strip(),
                s.get("webUrl") or "",
            )
            if len(out) >= max_sites:
                break
    except Exception:
        logger.exception("Graph site search failed — using configured site only")
    return out[:max_sites]


async def _walk_drive_files(
    *,
    site_id: str,
    item_path: str,
    folder_label: str,
    site_name: str,
    site_web_url: str,
    source: str,
    depth: int,
    max_depth: int,
    remaining: int,
) -> list[dict[str, Any]]:
    if remaining <= 0 or depth > max_depth:
        return []
    try:
        items = await ms_graph.graph_paginate(
            item_path,
            params={
                "$top": min(50, max(remaining, 10)),
                "$select": "id,name,webUrl,size,file,folder,lastModifiedDateTime,parentReference",
            },
            max_pages=2,
        )
    except Exception:
        logger.exception("Drive list failed path=%s", item_path[:80])
        return []

    out: list[dict[str, Any]] = []
    for it in items:
        if remaining - len(out) <= 0:
            break
        if it.get("folder") is not None and not it.get("file"):
            fid = it.get("id")
            fname = it.get("name") or ""
            if not fid or depth >= max_depth:
                continue
            child_folder = "/".join(p for p in (folder_label, fname) if p)
            nested = await _walk_drive_files(
                site_id=site_id,
                item_path=f"/sites/{site_id}/drive/items/{fid}/children",
                folder_label=child_folder,
                site_name=site_name,
                site_web_url=site_web_url,
                source=source,
                depth=depth + 1,
                max_depth=max_depth,
                remaining=remaining - len(out),
            )
            out.extend(nested)
            continue
        row = _drive_item_to_catalog(
            it,
            source=source,
            site_name=site_name,
            site_web_url=site_web_url,
            folder=folder_label,
        )
        if row:
            out.append(row)
    return out[:remaining]


async def list_sharepoint_files(*, site_id: str, limit: int) -> list[dict[str, Any]]:
    try:
        sid, name, url = await _site_meta(site_id)
    except Exception:
        sid, name, url = site_id, "SharePoint", ""
    depth = max(1, int(settings.doc_knowledge_folder_depth or 3))
    return await _walk_drive_files(
        site_id=sid,
        item_path=f"/sites/{sid}/drive/root/children",
        folder_label="",
        site_name=name,
        site_web_url=url,
        source=SOURCE_SHAREPOINT,
        depth=0,
        max_depth=depth,
        remaining=limit,
    )


async def search_sharepoint_files(
    *, site_id: str, query: str, limit: int, site_name: str = "", site_web_url: str = ""
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    q = re.sub(r"[^a-zA-Z0-9._\- ]+", " ", q).strip()
    if not q:
        return await list_sharepoint_files(site_id=site_id, limit=limit)
    try:
        items = await ms_graph.graph_paginate(
            f"/sites/{site_id}/drive/root/search(q='{q.replace(chr(39), ' ')}')",
            params={
                "$top": min(limit, 50),
                "$select": "id,name,webUrl,size,file,folder,lastModifiedDateTime,parentReference",
            },
            max_pages=2,
        )
    except Exception:
        logger.exception("Drive search failed site=%s q=%s", site_id[:12], q[:40])
        return await list_sharepoint_files(site_id=site_id, limit=limit)
    out: list[dict[str, Any]] = []
    for it in items:
        row = _drive_item_to_catalog(
            it,
            source=SOURCE_SHAREPOINT,
            site_name=site_name,
            site_web_url=site_web_url,
        )
        if row:
            out.append(row)
        if len(out) >= limit:
            break
    return out[:limit]


async def search_onedrive_files(*, query: str, limit: int) -> list[dict[str, Any]]:
    user = (settings.ms_graph_onedrive_user_id or "").strip()
    if not user:
        return []
    q = (query or "").strip()
    q = re.sub(r"[^a-zA-Z0-9._\- ]+", " ", q).strip()
    if not q:
        return await list_onedrive_files(limit=limit)
    try:
        items = await ms_graph.graph_paginate(
            f"/users/{user}/drive/root/search(q='{q.replace(chr(39), ' ')}')",
            params={
                "$top": min(limit, 50),
                "$select": "id,name,webUrl,size,file,folder,lastModifiedDateTime,parentReference",
            },
            max_pages=2,
        )
    except Exception:
        logger.exception("OneDrive search failed q=%s", q[:40])
        od = await list_onedrive_files(limit=max(limit, 50))
        ql = q.lower()
        return [
            r
            for r in od
            if ql in (r.get("title") or "").lower() or ql in (r.get("folder") or "").lower()
        ][:limit]
    out: list[dict[str, Any]] = []
    for it in items:
        row = _drive_item_to_catalog(it, source=SOURCE_ONEDRIVE, site_name="OneDrive", folder="")
        if row:
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
        row = _drive_item_to_catalog(
            it, source=SOURCE_ONEDRIVE, site_name="OneDrive", folder=""
        )
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
                "site_name": "OneNote",
                "site_web_url": web,
                "item_id": p.get("id") or "",
                "drive_id": "",
            }
        )
        if len(out) >= limit:
            break
    return out


async def list_knowledge_catalog(
    *,
    limit: int | None = None,
    query: str | None = None,
    keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Unified catalog for Teams selection (numbered list). Optional filename keyword search."""
    if not ms_graph.graph_configured():
        raise RuntimeError("Microsoft Graph credentials not configured")
    cap = int(limit or settings.doc_knowledge_max_list or 100)
    cap = max(10, min(cap, 250))
    sources = _enabled_sources()
    display_q = (query or "").strip()
    kws = list(keywords or [])
    if not kws and display_q:
        kws = tokenize_search_keywords(display_q)
    if not display_q and kws:
        display_q = " · ".join(kws)
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            key = f"{row.get('drive_id')}:{row.get('item_id') or row.get('external_id')}"
            if key in seen:
                continue
            seen.add(key)
            catalog.append(row)

    search_terms: list[str] = []
    if kws:
        search_terms = list(dict.fromkeys(kws))
        if display_q and len(display_q) >= 3 and display_q not in search_terms:
            search_terms.append(display_q)

    if SOURCE_SHAREPOINT in sources:
        max_sites = max(1, int(settings.doc_knowledge_max_sites or 8))
        sites = await list_accessible_sites(max_sites=max_sites)
        if not sites:
            try:
                sid, url = await resolve_site_id()
                sites = [{"id": sid, "name": "SharePoint", "web_url": url}]
            except Exception:
                sites = []
        per_site = max(8, cap // max(1, len(sites)))
        for site in sites:
            sid = site["id"]
            if search_terms:
                for term in search_terms[:6]:
                    _add(
                        await search_sharepoint_files(
                            site_id=sid,
                            query=term,
                            limit=per_site,
                            site_name=site.get("name") or "",
                            site_web_url=site.get("web_url") or "",
                        )
                    )
                    if len(catalog) >= cap:
                        break
            elif display_q:
                _add(
                    await search_sharepoint_files(
                        site_id=sid,
                        query=display_q,
                        limit=per_site,
                        site_name=site.get("name") or "",
                        site_web_url=site.get("web_url") or "",
                    )
                )
            else:
                _add(await list_sharepoint_files(site_id=sid, limit=per_site))
            if len(catalog) >= cap:
                break

    if SOURCE_ONEDRIVE in sources:
        if search_terms:
            for term in search_terms[:4]:
                _add(await search_onedrive_files(query=term, limit=max(8, cap // 4)))
        elif display_q:
            _add(await search_onedrive_files(query=display_q, limit=max(8, cap // 4)))
        else:
            _add(await list_onedrive_files(limit=max(8, cap // 4)))

    if SOURCE_ONENOTE in sources:
        try:
            site_id, _ = await resolve_site_id()
            on = await list_onenote_pages(site_id=site_id, limit=max(8, cap // 4))
            if kws:
                on = [r for r in on if row_matches_keywords(r, kws)]
            elif display_q:
                ql = display_q.lower()
                on = [r for r in on if ql in (r.get("title") or "").lower()]
            _add(on)
        except Exception:
            logger.exception("OneNote catalog skipped")

    if kws and not catalog:
        logger.info(
            "Graph keyword search returned no files; listing drives for local keyword match"
        )
        fallback_cap = min(cap * 3, 250)
        if SOURCE_SHAREPOINT in sources:
            fb_sites = await list_accessible_sites(
                max_sites=max(1, int(settings.doc_knowledge_max_sites or 8))
            )
            if not fb_sites:
                try:
                    sid, url = await resolve_site_id()
                    fb_sites = [{"id": sid, "name": "SharePoint", "web_url": url}]
                except Exception:
                    fb_sites = []
            per_site = max(25, fallback_cap // max(1, len(fb_sites)))
            for site in fb_sites:
                _add(await list_sharepoint_files(site_id=site["id"], limit=per_site))
                if len(catalog) >= fallback_cap:
                    break
        if SOURCE_ONEDRIVE in sources and len(catalog) < fallback_cap:
            _add(await list_onedrive_files(limit=max(25, fallback_cap // 2)))

    if kws:
        catalog = [r for r in catalog if row_matches_keywords(r, kws)]
    elif display_q:
        ql = display_q.lower()
        catalog = [
            r
            for r in catalog
            if ql in (r.get("title") or "").lower()
            or ql in (r.get("folder") or "").lower()
            or ql in (r.get("site_name") or "").lower()
        ]

    catalog.sort(
        key=lambda r: (
            -keyword_match_score(r, kws) if kws else 0,
            r.get("source_type") or "",
            (r.get("site_name") or "").lower(),
            (r.get("title") or "").lower(),
        )
    )
    for i, row in enumerate(catalog[:cap], start=1):
        row["pick"] = i
    return catalog[:cap]


async def list_meeting_transcripts(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Discover Teams `.vtt` transcripts (delegates to meeting_transcripts service)."""
    from agent.services import meeting_transcripts

    return await meeting_transcripts.list_meeting_transcript_files(limit=limit)


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


def _drive_content_path(entry: dict[str, Any], site_id: str) -> str:
    drive_id = entry.get("drive_id") or ""
    item_id = entry.get("item_id") or entry.get("external_id") or ""
    if drive_id and item_id:
        return f"/drives/{drive_id}/items/{item_id}/content"
    if item_id:
        return f"/sites/{site_id}/drive/items/{item_id}/content"
    return ""


async def fetch_document_bytes(entry: dict[str, Any]) -> tuple[bytes, str]:
    """Download original file bytes (Excel/Office). Does not extract text."""
    if entry.get("source_type") == SOURCE_ONENOTE:
        return b"", "onenote_not_workbook"
    site_id, _ = await resolve_site_id()
    path = _drive_content_path(entry, site_id)
    if not path:
        return b"", "missing_item_id"
    raw, ct = await ms_graph.graph_request_bytes("GET", path)
    return raw, ct or ""


async def upload_site_drive_item(
    *,
    folder: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> dict[str, Any] | None:
    """Upload bytes under the configured SharePoint site drive. Returns catalog row or None."""
    import httpx

    if not ms_graph.graph_configured():
        return None
    site_id, _ = await resolve_site_id()
    sid, site_name, site_web_url = await _site_meta(site_id)
    folder = (folder or "Analytics").strip().strip("/")
    try:
        await ms_graph.graph_request(
            "POST",
            f"/sites/{sid}/drive/root/children",
            json_body={
                "name": folder,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
        )
    except Exception:
        logger.info("%s folder may already exist", folder)

    safe = "".join(ch if ch.isalnum() or ch in ".-_" else "-" for ch in filename)[:80]
    path = quote(f"/{folder}/{safe}", safe="/")
    token = await ms_graph.get_app_token()
    url = (
        f"https://graph.microsoft.com/v1.0/sites/{sid}/drive/root:{path}:/content"
        "?@microsoft.graph.conflictBehavior=replace"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type or "application/octet-stream",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.put(url, headers=headers, content=content)
        if resp.status_code >= 400:
            logger.error("Site file upload failed %s %s", resp.status_code, resp.text[:400])
            return None
        item = resp.json()
    row = _drive_item_to_catalog(
        item,
        source=SOURCE_SHAREPOINT,
        site_name=site_name,
        site_web_url=site_web_url,
        folder=folder,
    )
    if row:
        row["upload_source"] = "teams_chat"
    return row


async def upload_site_file(
    *,
    folder: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> str:
    """Upload bytes under the configured SharePoint site drive. Returns webUrl or empty."""
    row = await upload_site_drive_item(
        folder=folder,
        filename=filename,
        content=content,
        content_type=content_type,
    )
    return (row or {}).get("web_url") or ""
