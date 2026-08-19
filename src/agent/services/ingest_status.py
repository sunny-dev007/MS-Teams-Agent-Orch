"""Ingestion overlay for Doc Library — raw vs ingested vs stale (source newer than KB).

Does not change Graph listing or WhatsApp/Dev paths. Used only when rendering
the catalog and when choosing graceful reindex vs skip-fresh.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent.models import knowledge_doc as kd

STATUS_RAW = "raw"
STATUS_INGESTED = "ingested"
STATUS_STALE = "stale"
STATUS_FAILED = "failed"
STATUS_INGESTING = "ingesting"

LABELS = {
    STATUS_RAW: "Raw / Not ingested",
    STATUS_INGESTED: "Ingested",
    STATUS_STALE: "Ready for re-ingest",
    STATUS_FAILED: "Ingest failed",
    STATUS_INGESTING: "Ingesting",
}


def parse_dt(raw: str | None) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def classify_ingest_status(entry: dict[str, Any], kb: dict[str, Any] | None) -> str:
    """Compare live Graph lastModified/size with stored ingest snapshot."""
    if not kb:
        return STATUS_RAW
    st = (kb.get("status") or "").lower()
    if st == kd.STATUS_INGESTING:
        return STATUS_INGESTING
    if st == kd.STATUS_FAILED:
        return STATUS_FAILED
    if st != kd.STATUS_READY:
        return STATUS_RAW

    meta = kb.get("metadata") or {}
    stored_lm = parse_dt(str(meta.get("source_last_modified") or meta.get("last_modified") or ""))
    graph_lm = parse_dt(str(entry.get("last_modified") or ""))
    try:
        stored_size = int(meta.get("source_size") if meta.get("source_size") is not None else meta.get("size") or 0)
    except (TypeError, ValueError):
        stored_size = 0
    try:
        graph_size = int(entry.get("size") or 0)
    except (TypeError, ValueError):
        graph_size = 0

    if graph_lm and stored_lm and graph_lm > stored_lm:
        return STATUS_STALE
    if graph_size and stored_size and graph_size != stored_size:
        return STATUS_STALE
    if graph_lm and not stored_lm:
        kb_updated = parse_dt(kb.get("updated_at"))
        if kb_updated and graph_lm > kb_updated:
            return STATUS_STALE
    return STATUS_INGESTED


def overlay_catalog(catalog: list[dict[str, Any]], kb_map: dict[tuple[str, str], dict]) -> list[dict[str, Any]]:
    for row in catalog:
        ext = row.get("external_id") or row.get("item_id") or ""
        src = row.get("source_type") or "sharepoint"
        kb = kb_map.get((ext, src))
        status = classify_ingest_status(row, kb)
        row["ingest_status"] = status
        row["ingest_label"] = LABELS.get(status, LABELS[STATUS_RAW])
        if kb:
            row["kb_doc_id"] = kb.get("id")
    return catalog


async def annotate_catalog(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = [
        (r.get("external_id") or r.get("item_id") or "", r.get("source_type") or "sharepoint")
        for r in catalog
    ]
    kb_map = await kd.map_by_external_ids(pairs)
    return overlay_catalog(catalog, kb_map)


def stale_picks(catalog: list[dict[str, Any]]) -> list[int]:
    out: list[int] = []
    for row in catalog:
        if row.get("ingest_status") == STATUS_STALE:
            try:
                out.append(int(row.get("pick") or 0))
            except (TypeError, ValueError):
                continue
    return [p for p in out if p]


def excel_picks(catalog: list[dict[str, Any]]) -> list[int]:
    out: list[int] = []
    for row in catalog:
        ext = (row.get("extension") or "").lower().lstrip(".")
        title = (row.get("title") or "").lower()
        if ext in ("xlsx", "xlsm", "csv") or title.endswith((".xlsx", ".xlsm", ".csv")):
            try:
                out.append(int(row.get("pick") or 0))
            except (TypeError, ValueError):
                continue
    return [p for p in out if p]
