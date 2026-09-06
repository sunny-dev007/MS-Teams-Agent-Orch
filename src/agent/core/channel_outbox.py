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


async def list_pending_outbox_sessions(*, prefix: str = "teams:", limit: int = 50) -> list[str]:
    """Distinct session ids with undelivered outbox (oldest activity first)."""
    await ensure_db_schema()
    async with async_session() as db:
        rows = (
            await db.execute(
                select(ChannelOutbox.session_id)
                .where(
                    ChannelOutbox.delivered.is_(False),
                    ChannelOutbox.session_id.startswith(prefix),
                )
                .group_by(ChannelOutbox.session_id)
                .order_by(func.min(ChannelOutbox.id).asc())
                .limit(limit)
            )
        ).all()
        return [str(r[0]) for r in rows if r and r[0]]


async def peek_outbox_texts(
    session_id: str,
    *,
    limit: int = 20,
    min_age_seconds: int = 0,
) -> list[tuple[int, str]]:
    """Undelivered (id, text) pairs oldest-first — does not mark delivered.

    ``min_age_seconds`` skips fresh rows so an active Orbit HTTP turn can drain
    them first (avoids duplicate proactive + in-turn delivery).
    """
    if not session_id:
        return []
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
                .limit(limit * 3 if min_age_seconds > 0 else limit)
            )
        ).scalars().all()
        out: list[tuple[int, str]] = []
        now = datetime.datetime.now(datetime.timezone.utc)
        for row in rows:
            if min_age_seconds > 0 and row.created_at is not None:
                created = row.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=datetime.timezone.utc)
                age = (now - created).total_seconds()
                if age < min_age_seconds:
                    continue
            out.append((int(row.id), str(row.text)))
            if len(out) >= limit:
                break
        return out


async def mark_outbox_ids_delivered(ids: list[int]) -> int:
    if not ids:
        return 0
    await ensure_db_schema()
    async with async_session() as db:
        result = await db.execute(
            update(ChannelOutbox)
            .where(
                ChannelOutbox.id.in_(ids),
                ChannelOutbox.delivered.is_(False),
            )
            .values(delivered=True)
        )
        await db.commit()
        return int(result.rowcount or 0)
