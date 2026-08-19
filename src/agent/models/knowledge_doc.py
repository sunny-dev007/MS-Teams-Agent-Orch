"""Knowledge document + chunk store for Document Knowledge Fabric.

SQLite (same App Service pattern as ReleaseEvent). Vectors stored as JSON float lists.
Feature flag ENABLE_DOC_KNOWLEDGE defaults off — tables create harmlessly when schema ensures.
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.core.logging import get_logger
from agent.models.db import Base, async_session, ensure_db_schema

logger = get_logger(__name__)

STATUS_LISTED = "listed"
STATUS_INGESTING = "ingesting"
STATUS_READY = "ready"
STATUS_FAILED = "failed"


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(256), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)  # sharepoint|onedrive|onenote
    title: Mapped[str] = mapped_column(String(512), default="")
    web_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    doc_mode: Mapped[str] = mapped_column(String(32), default="general", index=True)
    status: Mapped[str] = mapped_column(String(32), default=STATUS_LISTED, index=True)
    extract_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_session: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    content_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def _parse_json_obj(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _parse_embedding(raw: str | None) -> list[float]:
    try:
        data = json.loads(raw or "[]")
        if isinstance(data, list):
            return [float(x) for x in data]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


def doc_to_dict(row: KnowledgeDocument) -> dict[str, Any]:
    return {
        "id": row.id,
        "external_id": row.external_id,
        "source_type": row.source_type,
        "title": row.title,
        "web_url": row.web_url,
        "mime_type": row.mime_type,
        "doc_mode": row.doc_mode,
        "status": row.status,
        "extract_status": row.extract_status,
        "owner_session": row.owner_session,
        "content_preview": row.content_preview,
        "metadata": _parse_json_obj(row.metadata_json),
        "chunk_count": row.chunk_count,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def chunk_to_dict(row: KnowledgeChunk, *, include_embedding: bool = False) -> dict[str, Any]:
    out = {
        "id": row.id,
        "doc_id": row.doc_id,
        "chunk_index": row.chunk_index,
        "text": row.text,
        "token_estimate": row.token_estimate,
    }
    if include_embedding:
        out["embedding"] = _parse_embedding(row.embedding_json)
    return out


async def upsert_document(
    *,
    external_id: str,
    source_type: str,
    title: str,
    web_url: str | None = None,
    mime_type: str | None = None,
    doc_mode: str = "general",
    status: str = STATUS_LISTED,
    extract_status: str | None = None,
    owner_session: str | None = None,
    content_preview: str | None = None,
    metadata: dict[str, Any] | None = None,
    chunk_count: int | None = None,
    error: str | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    await ensure_db_schema()
    async with async_session() as session:
        row = None
        if doc_id:
            row = await session.get(KnowledgeDocument, doc_id)
        if row is None:
            result = await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.external_id == external_id,
                    KnowledgeDocument.source_type == source_type,
                )
            )
            row = result.scalar_one_or_none()
        if row is None:
            row = KnowledgeDocument(
                id=doc_id or f"kd_{uuid.uuid4().hex[:16]}",
                external_id=external_id,
                source_type=source_type,
                title=title[:512],
            )
            session.add(row)

        row.title = (title or row.title)[:512]
        row.web_url = web_url if web_url is not None else row.web_url
        row.mime_type = mime_type if mime_type is not None else row.mime_type
        row.doc_mode = doc_mode or row.doc_mode
        row.status = status or row.status
        if extract_status is not None:
            row.extract_status = extract_status
        if owner_session is not None:
            row.owner_session = owner_session
        if content_preview is not None:
            row.content_preview = content_preview[:2000]
        if metadata is not None:
            row.metadata_json = json.dumps(metadata)
        if chunk_count is not None:
            row.chunk_count = chunk_count
        if error is not None:
            row.error = error
        await session.commit()
        await session.refresh(row)
        return doc_to_dict(row)


async def replace_chunks(
    doc_id: str,
    chunks: list[dict[str, Any]],
) -> int:
    """Replace all chunks for a document. Each chunk: text, embedding (list[float])."""
    await ensure_db_schema()
    async with async_session() as session:
        existing = await session.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id)
        )
        for old in existing.scalars().all():
            await session.delete(old)
        for i, ch in enumerate(chunks):
            emb = ch.get("embedding") or []
            text = ch.get("text") or ""
            session.add(
                KnowledgeChunk(
                    id=f"kc_{uuid.uuid4().hex[:16]}",
                    doc_id=doc_id,
                    chunk_index=i,
                    text=text,
                    embedding_json=json.dumps(emb),
                    token_estimate=max(1, len(text) // 4),
                )
            )
        doc = await session.get(KnowledgeDocument, doc_id)
        if doc:
            doc.chunk_count = len(chunks)
            doc.status = STATUS_READY if chunks else STATUS_FAILED
        await session.commit()
        return len(chunks)


async def map_by_external_ids(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Lookup knowledge rows by (external_id, source_type). Additive catalog overlay."""
    keys = [(e or "", s or "") for e, s in pairs if e]
    if not keys:
        return {}
    ids = list({k[0] for k in keys})
    await ensure_db_schema()
    async with async_session() as session:
        q = select(KnowledgeDocument).where(KnowledgeDocument.external_id.in_(ids))
        rows = (await session.execute(q)).scalars().all()
    out: dict[tuple[str, str], dict] = {}
    wanted = set(keys)
    for row in rows:
        key = (row.external_id, row.source_type)
        if key in wanted:
            out[key] = doc_to_dict(row)
    return out


async def list_ready_documents(*, owner_session: str | None = None, limit: int = 50) -> list[dict]:
    await ensure_db_schema()
    async with async_session() as session:
        q = select(KnowledgeDocument).where(KnowledgeDocument.status == STATUS_READY)
        if owner_session:
            q = q.where(KnowledgeDocument.owner_session == owner_session)
        q = q.order_by(KnowledgeDocument.updated_at.desc()).limit(limit)
        rows = (await session.execute(q)).scalars().all()
        return [doc_to_dict(r) for r in rows]


async def get_document(doc_id: str) -> dict[str, Any] | None:
    await ensure_db_schema()
    async with async_session() as session:
        row = await session.get(KnowledgeDocument, doc_id)
        return doc_to_dict(row) if row else None


async def load_chunks_with_embeddings(
    *,
    owner_session: str | None = None,
    doc_ids: list[str] | None = None,
    limit_docs: int = 100,
) -> list[dict[str, Any]]:
    """Load chunk rows joined with doc metadata for in-process similarity search."""
    await ensure_db_schema()
    async with async_session() as session:
        doc_q = select(KnowledgeDocument).where(KnowledgeDocument.status == STATUS_READY)
        if owner_session:
            doc_q = doc_q.where(KnowledgeDocument.owner_session == owner_session)
        if doc_ids:
            doc_q = doc_q.where(KnowledgeDocument.id.in_(doc_ids))
        doc_q = doc_q.limit(limit_docs)
        docs = (await session.execute(doc_q)).scalars().all()
        if not docs:
            return []
        by_id = {d.id: d for d in docs}
        chunk_q = select(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(list(by_id.keys())))
        chunks = (await session.execute(chunk_q)).scalars().all()
        out: list[dict[str, Any]] = []
        for c in chunks:
            d = by_id.get(c.doc_id)
            if not d:
                continue
            out.append(
                {
                    **chunk_to_dict(c, include_embedding=True),
                    "title": d.title,
                    "source_type": d.source_type,
                    "doc_mode": d.doc_mode,
                    "web_url": d.web_url,
                    "doc_id": d.id,
                }
            )
        return out
