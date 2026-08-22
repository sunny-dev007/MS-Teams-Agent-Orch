"""Meeting transcript cache for Meeting Intelligence Fabric.

Feature flag ENABLE_MEETING_INTELLIGENCE defaults off.
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
STATUS_PARSED = "parsed"
STATUS_FAILED = "failed"


class MeetingTranscript(Base):
    __tablename__ = "meeting_transcripts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(256), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    web_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    meeting_date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_name: Mapped[str] = mapped_column(String(256), default="")
    agenda_summary: Mapped[str] = mapped_column(String(512), default="")
    participants_json: Mapped[str] = mapped_column(Text, default="[]")
    vtt_hash: Mapped[str] = mapped_column(String(128), default="", index=True)
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    owner_session: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default=STATUS_LISTED, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def _parse_json(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _parse_list(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except json.JSONDecodeError:
        pass
    return []


def row_to_dict(row: MeetingTranscript) -> dict[str, Any]:
    return {
        "id": row.id,
        "external_id": row.external_id,
        "source_type": row.source_type,
        "title": row.title,
        "web_url": row.web_url,
        "meeting_date": row.meeting_date.isoformat() if row.meeting_date else "",
        "duration_seconds": row.duration_seconds,
        "client_name": row.client_name,
        "agenda_summary": row.agenda_summary,
        "participants": _parse_list(row.participants_json),
        "vtt_hash": row.vtt_hash,
        "status": row.status,
        "metadata": _parse_json(row.metadata_json),
    }


async def get_by_external_id(external_id: str) -> MeetingTranscript | None:
    await ensure_db_schema()
    ext = (external_id or "").strip()
    if not ext:
        return None
    async with async_session() as session:
        result = await session.execute(
            select(MeetingTranscript).where(MeetingTranscript.external_id == ext)
        )
        return result.scalar_one_or_none()


async def upsert_transcript(
    *,
    external_id: str,
    source_type: str,
    title: str,
    web_url: str = "",
    meeting_date: datetime.datetime | None = None,
    duration_seconds: int | None = None,
    client_name: str = "",
    agenda_summary: str = "",
    participants: list[str] | None = None,
    vtt_hash: str = "",
    parsed_text: str | None = None,
    metadata: dict[str, Any] | None = None,
    owner_session: str | None = None,
    status: str = STATUS_LISTED,
) -> dict[str, Any]:
    await ensure_db_schema()
    ext = (external_id or "").strip()
    if not ext:
        raise ValueError("external_id required")
    async with async_session() as session:
        result = await session.execute(
            select(MeetingTranscript).where(MeetingTranscript.external_id == ext)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = MeetingTranscript(
                id=str(uuid.uuid4()),
                external_id=ext,
                source_type=source_type,
                title=title,
            )
            session.add(row)
        row.source_type = source_type
        row.title = title
        row.web_url = web_url or row.web_url
        if meeting_date is not None:
            row.meeting_date = meeting_date
        if duration_seconds is not None:
            row.duration_seconds = duration_seconds
        if client_name:
            row.client_name = client_name
        if agenda_summary:
            row.agenda_summary = agenda_summary
        if participants is not None:
            row.participants_json = json.dumps(participants)
        if vtt_hash:
            row.vtt_hash = vtt_hash
        if parsed_text is not None:
            row.parsed_text = parsed_text
        if metadata is not None:
            row.metadata_json = json.dumps(metadata)
        if owner_session:
            row.owner_session = owner_session
        row.status = status
        await session.commit()
        await session.refresh(row)
        return row_to_dict(row)
