"""Discover and enrich Teams meeting transcripts from SharePoint / OneDrive.

Feature: Meeting Intelligence Fabric — ENABLE_MEETING_INTELLIGENCE default false.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.config import settings
from agent.core.logging import get_logger
from agent.models import meeting_transcript as mt
from agent.services import graph_docs, ms_graph, vtt_parse
from agent.services.llm import invoke_llm

logger = get_logger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_METADATA_PROMPT = (_PROMPT_DIR / "meeting_metadata_extract.txt").read_text(encoding="utf-8")

_VTT_NAME_RE = re.compile(r"transcript", re.I)


def _transcript_folders() -> list[str]:
    raw = (settings.meeting_transcript_folders or "Recordings,meeting transcript").strip()
    return [p.strip() for p in raw.split(",") if p.strip()]


def _is_transcript_file(row: dict[str, Any]) -> bool:
    title = (row.get("title") or "").lower()
    ext = (row.get("extension") or "").lower()
    if ext != ".vtt":
        return False
    return bool(_VTT_NAME_RE.search(title))


def _drive_key(row: dict[str, Any]) -> str:
    return f"{row.get('drive_id')}:{row.get('item_id') or row.get('external_id')}"


def _parse_iso_date(raw: str) -> datetime.datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


async def list_meeting_transcript_files(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Scan configured SharePoint / OneDrive folders for Teams .vtt transcripts."""
    if not ms_graph.graph_configured():
        raise RuntimeError("Microsoft Graph credentials not configured")
    cap = int(limit or settings.meeting_list_max or 30)
    cap = max(5, min(cap, 100))
    folders = _transcript_folders()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(batch: list[dict[str, Any]]) -> None:
        for row in batch:
            if not _is_transcript_file(row):
                continue
            key = _drive_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    # SharePoint: search per folder name + transcript keyword
    if graph_docs.SOURCE_SHAREPOINT in graph_docs._enabled_sources():
        sites = await graph_docs.list_accessible_sites(
            max_sites=max(1, int(settings.doc_knowledge_max_sites or 8))
        )
        if not sites:
            try:
                sid, url = await graph_docs.resolve_site_id()
                sites = [{"id": sid, "name": "SharePoint", "web_url": url}]
            except Exception:
                sites = []
        for site in sites:
            sid = site["id"]
            for folder in folders:
                _add(
                    await graph_docs.search_sharepoint_files(
                        site_id=sid,
                        query=folder,
                        limit=cap,
                        site_name=site.get("name") or "",
                        site_web_url=site.get("web_url") or "",
                    )
                )
                if len(rows) >= cap:
                    break
            if len(rows) >= cap:
                break

    # OneDrive Recordings folder
    if graph_docs.SOURCE_ONEDRIVE in graph_docs._enabled_sources() and len(rows) < cap:
        for folder in folders[:2]:
            _add(await graph_docs.search_onedrive_files(query=folder, limit=cap))
        _add(await graph_docs.search_onedrive_files(query="transcript", limit=cap))

    rows.sort(
        key=lambda r: (r.get("last_modified") or r.get("title") or ""),
        reverse=True,
    )
    for i, row in enumerate(rows[:cap], start=1):
        row["pick"] = i
    return rows[:cap]


async def download_and_parse(row: dict[str, Any]) -> tuple[list[dict], str, str]:
    """Download VTT bytes, parse cues, return (cues, plain_text, vtt_hash)."""
    raw, _ct = await graph_docs.fetch_document_bytes(row)
    vtt_hash = hashlib.sha256(raw).hexdigest()
    cues = vtt_parse.parse_vtt(raw)
    plain = vtt_parse.vtt_to_plain_text(cues)
    return cues, plain, vtt_hash


async def _extract_metadata_llm(
    *,
    filename: str,
    excerpt: str,
    participants_hint: list[str],
) -> dict[str, Any]:
    payload = (
        f"Filename: {filename}\n"
        f"Known speakers: {', '.join(participants_hint) or 'unknown'}\n\n"
        f"Transcript excerpt:\n{excerpt[:3000]}"
    )
    try:
        resp = await invoke_llm(
            [
                SystemMessage(content=_METADATA_PROMPT),
                HumanMessage(content=payload),
            ],
            temperature=0.1,
            role="planning",
        )
        text = (resp.content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        logger.exception("Meeting metadata LLM extract failed for %s", filename[:60])
    return {}


async def enrich_catalog_row(
    row: dict[str, Any],
    *,
    owner_session: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Merge Graph file row with cached / LLM metadata."""
    ext_id = _drive_key(row)
    cached = await mt.get_by_external_id(ext_id)
    heur = vtt_parse.vtt_metadata_heuristic(row.get("title") or "")

    if cached and not force_refresh:
        age_ok = True
        if cached.updated_at:
            hours = int(settings.meeting_metadata_cache_hours or 168)
            age = datetime.datetime.now(datetime.timezone.utc) - cached.updated_at.replace(
                tzinfo=datetime.timezone.utc
            )
            age_ok = age.total_seconds() < hours * 3600
        if age_ok and cached.vtt_hash and cached.client_name:
            out = dict(row)
            out.update(
                {
                    "meeting_id": cached.id,
                    "client_name": cached.client_name or "Unknown client",
                    "agenda_summary": cached.agenda_summary or "—",
                    "meeting_date": cached.meeting_date.isoformat() if cached.meeting_date else "",
                    "duration_minutes": (
                        int((cached.duration_seconds or 0) / 60) if cached.duration_seconds else None
                    ),
                    "participants": mt._parse_list(cached.participants_json),
                    "vtt_hash": cached.vtt_hash,
                }
            )
            return out

    cues, plain, vtt_hash = await download_and_parse(row)
    participants = vtt_parse.participants_from_cues(cues)
    duration = vtt_parse.duration_from_cues(cues)
    meta = await _extract_metadata_llm(
        filename=row.get("title") or "",
        excerpt=plain,
        participants_hint=participants,
    )
    client = (meta.get("client_name") or "").strip() or "Unknown client"
    agenda = (meta.get("agenda_summary") or "").strip() or "—"
    if meta.get("participants"):
        participants = [str(x) for x in meta["participants"] if str(x).strip()]

    meeting_dt = _parse_iso_date(str(meta.get("meeting_date_guess") or ""))
    if meeting_dt is None and heur.get("meeting_date"):
        meeting_dt = _parse_iso_date(str(heur["meeting_date"]))
    if meeting_dt is None:
        meeting_dt = _parse_iso_date(row.get("last_modified") or "")

    saved = await mt.upsert_transcript(
        external_id=ext_id,
        source_type=row.get("source_type") or "sharepoint",
        title=row.get("title") or "",
        web_url=row.get("web_url") or "",
        meeting_date=meeting_dt,
        duration_seconds=duration,
        client_name=client,
        agenda_summary=agenda,
        participants=participants,
        vtt_hash=vtt_hash,
        parsed_text=plain[:500_000] if plain else None,
        metadata={"drive_id": row.get("drive_id"), "item_id": row.get("item_id")},
        owner_session=owner_session,
        status=mt.STATUS_PARSED,
    )

    out = dict(row)
    out.update(
        {
            "meeting_id": saved["id"],
            "client_name": client,
            "agenda_summary": agenda,
            "meeting_date": saved.get("meeting_date") or "",
            "duration_minutes": int(duration / 60) if duration else None,
            "participants": participants,
            "vtt_hash": vtt_hash,
            "parsed_text": plain,
        }
    )
    return out


async def build_meeting_catalog(
    *,
    owner_session: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    files = await list_meeting_transcript_files(limit=limit)
    catalog: list[dict[str, Any]] = []
    for row in files:
        try:
            catalog.append(await enrich_catalog_row(row, owner_session=owner_session))
        except Exception:
            logger.exception("Failed enriching transcript %s", row.get("title"))
    return catalog


def format_meeting_date(raw: str) -> str:
    dt = _parse_iso_date(raw)
    if not dt:
        return raw[:16] if raw else "—"
    return dt.strftime("%d %b %Y %H:%M")


def format_duration(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"
