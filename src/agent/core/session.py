import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.models.db import Base, async_session


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    phone: Mapped[str] = mapped_column(String(32), primary_key=True)
    awaiting: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


async def get_session(phone: str) -> dict[str, Any]:
    async with async_session() as db:
        row = await db.get(ConversationSession, phone)
        if not row:
            return {"phone": phone, "awaiting": None, "provider": None, "data": {}}
        return {
            "phone": row.phone,
            "awaiting": row.awaiting,
            "provider": row.provider,
            "data": row.data_json or {},
        }


async def save_session(
    phone: str,
    *,
    awaiting: str | None = None,
    provider: str | None = None,
    data: dict[str, Any] | None = None,
    merge_data: bool = True,
    clear_awaiting: bool = False,
) -> None:
    async with async_session() as db:
        row = await db.get(ConversationSession, phone)
        if row is None:
            row = ConversationSession(phone=phone, data_json={})
            db.add(row)

        if clear_awaiting:
            row.awaiting = None
        elif awaiting is not None:
            row.awaiting = awaiting

        if provider is not None:
            row.provider = provider

        if data is not None:
            if merge_data and row.data_json:
                merged = dict(row.data_json)
                merged.update(data)
                row.data_json = merged
            else:
                row.data_json = data

        await db.commit()


async def clear_session(phone: str) -> None:
    async with async_session() as db:
        row = await db.get(ConversationSession, phone)
        if row:
            row.awaiting = None
            row.provider = None
            row.data_json = {}
            await db.commit()


async def list_recent_task_summaries(limit: int = 5) -> list[dict]:
    from agent.models.task import Task

    async with async_session() as db:
        result = await db.execute(
            select(Task).order_by(Task.updated_at.desc()).limit(limit)
        )
        rows = result.scalars().all()
        return [
            {
                "id": t.id,
                "status": t.status,
                "intent": t.intent,
                "repo_url": t.repo_url,
                "pr_url": t.pr_url,
            }
            for t in rows
        ]
