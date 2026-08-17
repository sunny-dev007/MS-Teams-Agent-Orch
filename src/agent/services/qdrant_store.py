"""Qdrant Cloud vector store for Document Knowledge Fabric.

Uses httpx REST (no new package). When QDRANT_URL + QDRANT_API_KEY are set and
ENABLE_QDRANT is true, ingest upserts points and RAG searches Qdrant.

SQLite remains the document catalog + chunk-text source of truth and the
offline fallback when Qdrant is unset or unreachable.

Security: never log the API key. Prefer a *cluster Database API key* with the
cluster REST URL — a Cloud Management key alone cannot write vectors.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import httpx

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

_COLLECTION_ENSURED: set[str] = set()


def qdrant_configured() -> bool:
    return bool(
        settings.enable_qdrant
        and (settings.qdrant_url or "").strip()
        and settings.qdrant_api_key.get_secret_value()
    )


def qdrant_ready() -> bool:
    return bool(settings.enable_doc_knowledge and qdrant_configured())


def _base_url() -> str:
    raw = (settings.qdrant_url or "").strip().rstrip("/")
    if not raw:
        return ""
    # Cloud endpoints often omit :6333 — REST API listens there by default
    if raw.startswith("https://") and ":6333" not in raw and ":443" not in raw:
        # If path-less host without port, append 6333
        host = raw[len("https://") :]
        if "/" not in host and ":" not in host:
            return f"{raw}:6333"
    return raw


def _headers() -> dict[str, str]:
    key = settings.qdrant_api_key.get_secret_value()
    return {
        "api-key": key,
        "Content-Type": "application/json",
    }


def _collection() -> str:
    name = (settings.qdrant_collection or "doc_knowledge_chunks").strip()
    return name or "doc_knowledge_chunks"


def point_id(doc_id: str, chunk_index: int) -> str:
    """Stable UUID for idempotent upserts (Qdrant accepts UUID string ids)."""
    digest = hashlib.sha1(f"{doc_id}:{chunk_index}".encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


async def _request(
    method: str,
    path: str,
    *,
    json_body: dict | list | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    url = f"{_base_url()}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, url, headers=_headers(), json=json_body)
        if resp.status_code >= 400:
            # Never echo api-key; truncate body for logs
            logger.error(
                "Qdrant %s %s -> %s %s",
                method,
                path,
                resp.status_code,
                (resp.text or "")[:400],
            )
            resp.raise_for_status()
        if not resp.content:
            return {}
        data = resp.json()
        return data if isinstance(data, dict) else {"result": data}


async def ensure_collection(vector_size: int) -> None:
    """Create collection if missing. Idempotent per process for a given size."""
    if not qdrant_configured():
        return
    coll = _collection()
    cache_key = f"{coll}:{vector_size}"
    if cache_key in _COLLECTION_ENSURED:
        return

    # Exists?
    try:
        info = await _request("GET", f"/collections/{coll}")
        result = info.get("result") or {}
        cfg = ((result.get("config") or {}).get("params") or {}).get("vectors") or {}
        size = cfg.get("size") if isinstance(cfg, dict) else None
        if size and int(size) != int(vector_size):
            raise RuntimeError(
                f"Qdrant collection '{coll}' has size={size}, "
                f"but embeddings are {vector_size}-d. "
                f"Rename QDRANT_COLLECTION or recreate the collection."
            )
        _COLLECTION_ENSURED.add(cache_key)
        return
    except httpx.HTTPStatusError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise

    await _request(
        "PUT",
        f"/collections/{coll}",
        json_body={
            "vectors": {
                "size": int(vector_size),
                "distance": "Cosine",
            }
        },
    )
    # Payload indexes for filtered search (best-effort)
    for field, schema in (
        ("doc_id", "keyword"),
        ("source_type", "keyword"),
        ("doc_mode", "keyword"),
        ("owner_session", "keyword"),
    ):
        try:
            await _request(
                "PUT",
                f"/collections/{coll}/index",
                json_body={"field_name": field, "field_schema": schema},
            )
        except Exception:
            logger.warning("Qdrant payload index %s skipped", field)

    _COLLECTION_ENSURED.add(cache_key)
    logger.info("Qdrant collection ready: %s (size=%s)", coll, vector_size)


async def delete_document_points(doc_id: str) -> None:
    if not qdrant_configured() or not doc_id:
        return
    coll = _collection()
    try:
        await _request(
            "POST",
            f"/collections/{coll}/points/delete",
            json_body={
                "filter": {
                    "must": [{"key": "doc_id", "match": {"value": doc_id}}],
                }
            },
        )
    except Exception:
        logger.exception("Qdrant delete points failed for doc_id=%s", doc_id)


async def upsert_document_chunks(
    *,
    doc_id: str,
    title: str,
    source_type: str,
    doc_mode: str,
    web_url: str | None,
    owner_session: str | None,
    records: list[dict[str, Any]],
) -> int:
    """Upsert chunk vectors. Returns number of points written (0 if Qdrant off)."""
    if not qdrant_configured() or not records:
        return 0

    vectors = [r.get("embedding") or [] for r in records]
    if not vectors or not vectors[0]:
        return 0
    dim = len(vectors[0])
    await ensure_collection(dim)
    await delete_document_points(doc_id)

    points = []
    for i, rec in enumerate(records):
        emb = rec.get("embedding") or []
        if len(emb) != dim:
            continue
        text = (rec.get("text") or "")[:8000]
        points.append(
            {
                "id": point_id(doc_id, i),
                "vector": emb,
                "payload": {
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "title": title,
                    "source_type": source_type,
                    "doc_mode": doc_mode,
                    "web_url": web_url or "",
                    "owner_session": owner_session or "",
                    "text": text,
                },
            }
        )

    # Batch upsert (Qdrant accepts large batches; keep modest for B1 egress)
    coll = _collection()
    batch = 64
    written = 0
    for start in range(0, len(points), batch):
        chunk = points[start : start + batch]
        await _request(
            "PUT",
            f"/collections/{coll}/points?wait=true",
            json_body={"points": chunk},
        )
        written += len(chunk)
    return written


async def search(
    query_embedding: list[float],
    *,
    top_k: int | None = None,
    owner_session: str | None = None,
    doc_ids: list[str] | None = None,
    source_type: str | None = None,
    doc_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Semantic search against Qdrant. Returns hit dicts compatible with RAG agent."""
    if not qdrant_configured() or not query_embedding:
        return []

    k = int(top_k or settings.doc_knowledge_top_k or 5)
    must: list[dict[str, Any]] = []
    if owner_session:
        must.append({"key": "owner_session", "match": {"value": owner_session}})
    if source_type:
        must.append({"key": "source_type", "match": {"value": source_type}})
    if doc_mode:
        must.append({"key": "doc_mode", "match": {"value": doc_mode}})
    if doc_ids:
        must.append({"key": "doc_id", "match": {"any": list(doc_ids)}})

    body: dict[str, Any] = {
        "vector": query_embedding,
        "limit": k,
        "with_payload": True,
        "with_vector": False,
    }
    if must:
        body["filter"] = {"must": must}

    coll = _collection()
    data = await _request(
        "POST",
        f"/collections/{coll}/points/search",
        json_body=body,
    )
    results = data.get("result") or []
    hits: list[dict[str, Any]] = []
    for row in results:
        payload = row.get("payload") or {}
        hits.append(
            {
                "id": str(row.get("id") or ""),
                "doc_id": payload.get("doc_id") or "",
                "chunk_index": int(payload.get("chunk_index") or 0),
                "text": payload.get("text") or "",
                "title": payload.get("title") or "",
                "source_type": payload.get("source_type") or "",
                "doc_mode": payload.get("doc_mode") or "",
                "web_url": payload.get("web_url") or "",
                "score": float(row.get("score") or 0.0),
                "embedding": [],
            }
        )
    return hits


async def health_ping() -> dict[str, Any]:
    """Lightweight readiness probe for /copilot/health fabric block."""
    if not qdrant_configured():
        return {"configured": False, "ok": False, "collection": None}
    try:
        coll = _collection()
        info = await _request("GET", f"/collections/{coll}", timeout=15.0)
        result = info.get("result") or {}
        return {
            "configured": True,
            "ok": True,
            "collection": coll,
            "points_count": result.get("points_count"),
            "status": result.get("status"),
        }
    except httpx.HTTPStatusError as exc:
        # 404 = collection not created yet (ok until first ingest)
        if exc.response is not None and exc.response.status_code == 404:
            return {
                "configured": True,
                "ok": True,
                "collection": _collection(),
                "points_count": 0,
                "status": "missing_will_create_on_ingest",
            }
        return {"configured": True, "ok": False, "error": f"HTTP {exc.response.status_code}"}
    except Exception as exc:
        return {"configured": True, "ok": False, "error": type(exc).__name__}
