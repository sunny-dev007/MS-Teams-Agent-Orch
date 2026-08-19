"""Doc Library Agent — list SharePoint / OneDrive / OneNote for Teams selection.

Feature: Document Knowledge Fabric. ENABLE_DOC_KNOWLEDGE default false.
"""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.channel_identity import is_teams_session
from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
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

_PAGE_NAV_RE = re.compile(
    r"^\s*(next(?:\s+page)?|more(?:\s+documents?)?|previous|"
    r"prev(?:ious)?\s*page|page\s+\d+)\s*$",
    re.I,
)


def _file_label(row: dict) -> str:
    title = (row.get("title") or "untitled").replace("|", "/")
    url = (row.get("web_url") or "").strip()
    if url:
        return f"[{title}]({url})"
    return f"**{title}**"


def _format_catalog_page(
    catalog: list[dict],
    *,
    page: int,
    page_size: int,
    query: str = "",
    teams: bool = True,
) -> str:
    total = len(catalog)
    if total == 0:
        if query:
            return (
                f"_No files matched **{query}**._\n\n"
                "Try a shorter keyword, or *list my documents* for the full library."
            )
        return (
            "_No files found. Check Graph site settings / OneDrive user / Notes permissions._"
        )

    start = page * page_size
    chunk = catalog[start : start + page_size]
    page_count = max(1, (total + page_size - 1) // page_size)
    head = (
        f"Found **{total}** file(s)"
        + (f" matching **{query}**" if query else "")
        + f" — showing **{start + 1}–{start + len(chunk)}** "
        f"(page **{page + 1}** of **{page_count}**, {page_size} per page)."
    )
    lines = [head, "", f"_Tags:_ {graph_docs.SOURCE_TAG_LEGEND}", ""]
    lines.append(
        "_Ingestion:_ **Raw / Not ingested** · **Ingested** · **Ready for re-ingest** "
        "(source newer than last vector index) · **Ingest failed**"
    )
    lines.append("")
    if teams:
        lines += [
            "| # | File | Type | Size | Site / folder | Ingestion |",
            "| :---: | :--- | :---: | ---: | :--- | :--- |",
        ]
        for row in chunk:
            src = graph_docs.source_tag(row.get("source_type"))
            ext = (row.get("extension") or "—").lstrip(".") or "—"
            loc = " / ".join(
                p for p in ((row.get("site_name") or ""), (row.get("folder") or "")) if p
            ) or "—"
            loc = loc.replace("|", "/")
            ingest = (row.get("ingest_label") or "Raw / Not ingested").replace("|", "/")
            lines.append(
                f"| {row.get('pick')} | [{src}] {_file_label(row)} | {ext} | "
                f"{graph_docs.format_file_size(row.get('size'))} | {loc} | {ingest} |"
            )
    else:
        for row in chunk:
            src = graph_docs.source_tag(row.get("source_type"))
            ext = (row.get("extension") or "").lstrip(".") or "file"
            loc = " / ".join(
                p for p in ((row.get("site_name") or ""), (row.get("folder") or "")) if p
            )
            url = (row.get("web_url") or "").strip()
            lines.append(
                f"{row.get('pick')}. [{src}] *{row.get('title')}* "
                f"({ext} · {graph_docs.format_file_size(row.get('size'))}"
                f"{(' · ' + loc) if loc else ''} · {row.get('ingest_label') or 'Raw / Not ingested'})"
            )
            if url:
                lines.append(f"   {url}")
    return "\n".join(lines)


def _related_prompts(
    *,
    page: int,
    page_count: int,
    query: str,
    has_items: bool,
    stale_picks: list[int] | None = None,
    excel_picks: list[int] | None = None,
) -> str:
    bits = []
    if has_items and page + 1 < page_count:
        bits.append(f"*next page* (page {page + 2})")
    if page > 0:
        bits.append("*previous page*")
    bits.append("*ingest 1,3* (use the **#** column)")
    bits.append("*ingest all* (skips unchanged ingested files)")
    stale_picks = stale_picks or []
    if stale_picks:
        sample = ",".join(str(n) for n in stale_picks[:6])
        bits.append(f"*reingest stale* (or ingest {sample})")
    if query:
        bits.append("*list my documents* (clear search)")
    else:
        bits.append("*find document keyword* (filename search)")
    bits.append("*ask docs …* after ingest")
    excel_picks = excel_picks or []
    if settings.enable_data_analyst_agent and excel_picks:
        bits.append(f"*convert excel {excel_picks[0]}* (Data Analyst)")
    return "**Next:** " + " · ".join(bits)


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
    extra = "all-sites" if settings.doc_knowledge_all_sites else "configured-site"
    return "_Sources:_ " + " · ".join(bits) + f" · _{extra}_"


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
    raw_msg = state.get("user_message") or ""
    phone = state.get("whatsapp_phone") or ""
    teams = is_teams_session(phone)
    page_size = max(5, min(int(settings.doc_knowledge_page_size or 10), 25))

    if "ingested" in msg or "knowledge base" in msg or "kb status" in msg:
        docs = await kd.list_ready_documents(owner_session=phone or None, limit=30)
        if not docs:
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

    session_data: dict = {}
    if phone:
        try:
            session_data = (await get_session(phone)).get("data") or {}
        except Exception:
            session_data = {}

    existing = list(session_data.get("doc_catalog") or [])
    current_page = int(session_data.get("doc_page") or 0)
    stored_query = str(session_data.get("doc_query") or "")
    is_page_nav = bool(_PAGE_NAV_RE.match(raw_msg.strip()))
    query = graph_docs.extract_library_query(raw_msg)

    catalog = existing
    if is_page_nav and existing:
        page_count = max(1, (len(existing) + page_size - 1) // page_size)
        current_page = graph_docs.parse_library_page(raw_msg, current_page, page_count)
        catalog = existing
        query = stored_query
    else:
        try:
            catalog = await graph_docs.list_knowledge_catalog(query=query or None)
        except Exception as exc:
            logger.exception("Doc library list failed")
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": (
                    f"*Doc Library Agent* failed listing: {exc}\n\n"
                    "If extra-site search is blocked, the configured SharePoint site is still used."
                ),
                "error": str(exc),
            }
        current_page = 0

    page_count = max(1, (len(catalog) + page_size - 1) // page_size)
    current_page = max(0, min(current_page, page_count - 1))

    try:
        from agent.services.ingest_status import annotate_catalog, excel_picks, stale_picks

        catalog = await annotate_catalog(catalog)
    except Exception:
        logger.exception("Ingestion status overlay failed — listing without status column extras")
        stale_nums: list[int] = []
        excel_nums: list[int] = []
    else:
        stale_nums = stale_picks(catalog)
        excel_nums = excel_picks(catalog)

    if phone:
        try:
            await save_session(
                phone,
                awaiting="doc_pick",
                data={
                    "doc_catalog": catalog,
                    "doc_page": current_page,
                    "doc_query": query,
                    "channel": "teams" if teams else "whatsapp",
                },
                merge_data=True,
            )
        except Exception:
            logger.exception("Failed saving doc_catalog session")

    body = _format_catalog_page(
        catalog,
        page=current_page,
        page_size=page_size,
        query=query,
        teams=teams,
    )
    note = (
        "*Doc Library Agent* — selectable documents\n\n"
        f"{body}\n\n"
        f"{_sources_footer()}\n\n"
        + _related_prompts(
            page=current_page,
            page_count=page_count,
            query=query,
            has_items=bool(catalog),
            stale_picks=stale_nums,
            excel_picks=excel_nums,
        )
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
