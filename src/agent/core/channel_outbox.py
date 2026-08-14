"""Outbound message outbox for Teams/Copilot (async progress without Bot Framework)."""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, select, update
from sqlalchemy.orm import Mapped, mapped_column

from agent.core.logging import get_logger
from agent.models.db import Base, async_session, ensure_db_schema

logger = get_logger(__name__)


class ChannelOutbox(Base):
    __tablename__ = "channel_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


async def enqueue_outbox(session_id: str, text: str) -> None:
    if not session_id or not text:
        return
    await ensure_db_schema()
    async with async_session() as db:
        db.add(ChannelOutbox(session_id=session_id, text=text, delivered=False))
        await db.commit()
    logger.info("Outbox enqueue session=%s chars=%s", session_id, len(text))


async def drain_outbox(session_id: str, *, limit: int = 20) -> list[str]:
    """Return undelivered messages oldest-first and mark them delivered.

    Uses a bulk UPDATE by id (not ORM row dirty-tracking) so concurrent Copilot
    polls cannot raise StaleDataError / HTTP 500 when two drains race.
    """
    if not session_id:
        return []
    try:
        await ensure_db_schema()
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(ChannelOutbox)
                    .where(
                        ChannelOutbox.session_id == session_id,
                        ChannelOutbox.delivered.is_(False),
                    )
                    .order_by(ChannelOutbox.id.asc())
                    .limit(limit)
                )
            ).scalars().all()
            if not rows:
                return []
            texts = [row.text for row in rows]
            ids = [row.id for row in rows]
            await db.execute(
                update(ChannelOutbox)
                .where(
                    ChannelOutbox.id.in_(ids),
                    ChannelOutbox.delivered.is_(False),
                )
                .values(delivered=True)
            )
            await db.commit()
            return texts
    except Exception:
        logger.exception("drain_outbox failed for session=%s", session_id)
        return []


async def peek_outbox_count(session_id: str) -> int:
    if not session_id:
        return 0
    await ensure_db_schema()
    async with async_session() as db:
        rows = (
            await db.execute(
                select(ChannelOutbox.id).where(
                    ChannelOutbox.session_id == session_id,
                    ChannelOutbox.delivered.is_(False),
                )
            )
        ).all()
        return len(rows)
