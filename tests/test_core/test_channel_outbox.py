"""Channel outbox drain must not raise under concurrent Copilot polls."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.core import channel_outbox as outbox
from agent.models.db import Base


@pytest.fixture()
async def outbox_db(tmp_path, monkeypatch):
    db_path = tmp_path / "outbox.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Register outbox table on this engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _noop_schema():
        return None

    monkeypatch.setattr(outbox, "async_session", session_factory)
    monkeypatch.setattr(outbox, "ensure_db_schema", _noop_schema)
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_and_drain_once(outbox_db):
    await outbox.enqueue_outbox("teams:user-1", "hello repos")
    first = await outbox.drain_outbox("teams:user-1")
    second = await outbox.drain_outbox("teams:user-1")
    assert first == ["hello repos"]
    assert second == []


@pytest.mark.asyncio
async def test_concurrent_drain_never_raises(outbox_db):
    """Two overlapping drains must not raise StaleDataError (HTTP 500 root cause)."""
    await outbox.enqueue_outbox("teams:user-race", "provider picker")

    results = await asyncio.gather(
        outbox.drain_outbox("teams:user-race"),
        outbox.drain_outbox("teams:user-race"),
        return_exceptions=True,
    )
    assert not any(isinstance(r, BaseException) for r in results)
    delivered = [t for batch in results if isinstance(batch, list) for t in batch]
    assert "provider picker" in delivered
    # After both complete, nothing left undelivered
    assert await outbox.drain_outbox("teams:user-race") == []


@pytest.mark.asyncio
async def test_drain_failure_returns_empty(monkeypatch):
    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(outbox, "ensure_db_schema", boom)
    assert await outbox.drain_outbox("teams:x") == []
