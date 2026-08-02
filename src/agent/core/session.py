import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Mapped, mapped_column

from agent.core.logging import get_logger
from agent.models.db import Base, async_session, ensure_db_schema

logger = get_logger(__name__)


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    phone: Mapped[str] = mapped_column(String(32), primary_key=True)
    awaiting: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def _empty_session(phone: str) -> dict[str, Any]:
    return {"phone": phone, "awaiting": None, "provider": None, "data": {}}


async def _with_schema_retry(op_name: str, coro_factory):
    """Run a DB op; if tables are missing after deploy, create schema and retry once."""
    try:
        return await coro_factory()
    except (OperationalError, ProgrammingError) as e:
        msg = str(e).lower()
        if "no such table" not in msg and "does not exist" not in msg:
            raise
        logger.warning("%s hit missing schema (%s) — ensuring tables and retrying", op_name, e)
        await ensure_db_schema()
        return await coro_factory()


async def get_session(phone: str) -> dict[str, Any]:
    async def _read() -> dict[str, Any]:
        async with async_session() as db:
            row = await db.get(ConversationSession, phone)
            if not row:
                return _empty_session(phone)
            return {
                "phone": row.phone,
                "awaiting": row.awaiting,
                "provider": row.provider,
                "data": row.data_json or {},
            }

    try:
        session = await _with_schema_retry("get_session", _read)
    except Exception:
        # Never break WhatsApp hello / planner on transient DB issues
        logger.exception("get_session failed for %s — returning empty session", phone)
        session = _empty_session(phone)

    # App Service recycles mid-request; durable JSON on /home/site/data survives
    # SQLite visibility races between overlapping containers.
    try:
        from agent.core.gate_store import merge_session_with_gate

        return merge_session_with_gate(session)
    except Exception:
        logger.exception("gate_store merge failed for %s", phone)
        return session


_WORKFLOW_GATE_NAMES = frozenset(
    {
        "plan_approval",
        "pr_review_mode",
        "manual_pr_review",
        "approval",
    }
)


async def save_session(
    phone: str,
    *,
    awaiting: str | None = None,
    provider: str | None = None,
    data: dict[str, Any] | None = None,
    merge_data: bool = True,
    clear_awaiting: bool = False,
) -> None:
    # Write durable gate BEFORE SQLite so App Service recycle mid-commit still
    # leaves a recoverable PR/plan gate for the next container.
    try:
        from agent.core.gate_store import clear_gate, write_gate

        if clear_awaiting:
            clear_gate(phone)
        elif awaiting in _WORKFLOW_GATE_NAMES:
            write_gate(
                phone,
                awaiting=awaiting or "",
                provider=provider or "",
                data=data or {},
            )
    except Exception:
        logger.exception("Durable gate pre-write failed for %s", phone)

    async def _write() -> None:
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
            logger.info(
                "Saved session phone=%s awaiting=%s task=%s",
                phone,
                row.awaiting,
                (row.data_json or {}).get("pending_task_id"),
            )
            # Re-mirror after merge so durable file has full session data.
            try:
                from agent.core.gate_store import clear_gate, write_gate

                if clear_awaiting or row.awaiting is None:
                    clear_gate(phone)
                elif row.awaiting in _WORKFLOW_GATE_NAMES:
                    write_gate(
                        phone,
                        awaiting=row.awaiting,
                        provider=row.provider or "",
                        data=row.data_json or {},
                    )
            except Exception:
                logger.exception("Durable gate mirror failed for %s", phone)

    try:
        await _with_schema_retry("save_session", _write)
    except Exception:
        logger.exception("save_session failed for %s awaiting=%s", phone, awaiting)
        raise


async def clear_session(phone: str) -> None:
    async def _clear() -> None:
        async with async_session() as db:
            row = await db.get(ConversationSession, phone)
            if row:
                row.awaiting = None
                row.provider = None
                row.data_json = {}
                await db.commit()

    try:
        await _with_schema_retry("clear_session", _clear)
    except Exception:
        logger.exception("clear_session failed for %s", phone)
    try:
        from agent.core.gate_store import clear_gate

        clear_gate(phone)
    except Exception:
        logger.exception("Durable gate clear failed for %s", phone)


async def list_recent_task_summaries(limit: int = 5) -> list[dict]:
    from agent.models.task import Task

    async def _list() -> list[dict]:
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

    try:
        return await _with_schema_retry("list_recent_task_summaries", _list)
    except Exception:
        logger.exception("list_recent_task_summaries failed")
        return []

