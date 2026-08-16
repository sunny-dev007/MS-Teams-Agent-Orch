"""Release Event store — correlation object for Dev → QA → Docs fabric.

Feature: Release Agent Fabric (flags default off — no prod behavior change).
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from sqlalchemy import DateTime, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.core.logging import get_logger
from agent.models.db import Base, async_session, ensure_db_schema

logger = get_logger(__name__)

# Status machine (v1)
STATUS_DEPLOYED = "deployed"
STATUS_QA_PENDING = "qa_pending"
STATUS_QA_RUNNING = "qa_running"
STATUS_QA_PASSED = "qa_passed"
STATUS_QA_FAILED = "qa_failed"
STATUS_DOCS_PENDING = "docs_pending"
STATUS_DOCS_PUBLISHED = "docs_published"


class ReleaseEvent(Base):
    __tablename__ = "release_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pr_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    pipeline_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    build_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    env: Mapped[str] = mapped_column(String(32), default="prod")
    app_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=STATUS_DEPLOYED, index=True)
    artifacts_json: Mapped[str] = mapped_column(Text, default="[]")
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="teams")
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    qa_report_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def _new_release_id() -> str:
    return f"rel_{uuid.uuid4().hex[:16]}"


def _parse_json_list(raw: str | None) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _parse_json_obj(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def to_dict(row: ReleaseEvent) -> dict[str, Any]:
    return {
        "release_id": row.id,
        "pr_id": row.pr_id,
        "pipeline_id": row.pipeline_id,
        "build_id": row.build_id,
        "commit_sha": row.commit_sha,
        "env": row.env,
        "app_url": row.app_url,
        "status": row.status,
        "artifacts": _parse_json_list(row.artifacts_json),
        "requested_by": row.requested_by,
        "channel": row.channel,
        "title": row.title,
        "summary": row.summary,
        "doc_url": row.doc_url,
        "qa_report_url": row.qa_report_url,
        "extra": _parse_json_obj(row.extra_json),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def upsert_release_event(
    *,
    release_id: str | None = None,
    pr_id: str | None = None,
    pipeline_id: str | None = None,
    build_id: str | None = None,
    commit_sha: str | None = None,
    env: str = "prod",
    app_url: str | None = None,
    status: str = STATUS_DEPLOYED,
    artifacts: list[dict[str, Any]] | None = None,
    requested_by: str | None = None,
    channel: str = "teams",
    title: str | None = None,
    summary: str | None = None,
    doc_url: str | None = None,
    qa_report_url: str | None = None,
    extra: dict[str, Any] | None = None,
    merge: bool = True,
) -> dict[str, Any]:
    """Create or update a release event. Prefer matching build_id then pipeline+pr."""
    await ensure_db_schema()
    async with async_session() as db:
        row: ReleaseEvent | None = None
        if release_id:
            row = await db.get(ReleaseEvent, release_id)
        if row is None and build_id:
            row = (
                await db.execute(select(ReleaseEvent).where(ReleaseEvent.build_id == build_id))
            ).scalars().first()
        if row is None and pipeline_id and pr_id:
            row = (
                await db.execute(
                    select(ReleaseEvent).where(
                        ReleaseEvent.pipeline_id == pipeline_id,
                        ReleaseEvent.pr_id == pr_id,
                    )
                )
            ).scalars().first()

        if row is None:
            row = ReleaseEvent(id=release_id or _new_release_id())
            db.add(row)

        def _set(attr: str, value: Any) -> None:
            if value is None and merge:
                return
            setattr(row, attr, value)

        _set("pr_id", pr_id)
        _set("pipeline_id", pipeline_id)
        _set("build_id", build_id)
        _set("commit_sha", commit_sha)
        if env:
            row.env = env
        _set("app_url", app_url)
        if status:
            row.status = status
        if artifacts is not None:
            row.artifacts_json = json.dumps(artifacts)
        _set("requested_by", requested_by)
        if channel:
            row.channel = channel
        _set("title", title)
        _set("summary", summary)
        _set("doc_url", doc_url)
        _set("qa_report_url", qa_report_url)
        if extra is not None:
            existing = _parse_json_obj(row.extra_json)
            existing.update(extra)
            row.extra_json = json.dumps(existing)

        await db.commit()
        await db.refresh(row)
        logger.info(
            "ReleaseEvent upsert id=%s status=%s pr=%s build=%s",
            row.id,
            row.status,
            row.pr_id,
            row.build_id,
        )
        return to_dict(row)


async def get_release_event(release_id: str) -> dict[str, Any] | None:
    if not release_id:
        return None
    await ensure_db_schema()
    async with async_session() as db:
        row = await db.get(ReleaseEvent, release_id)
        return to_dict(row) if row else None


async def find_release_event(
    *,
    pr_id: str | None = None,
    pipeline_id: str | None = None,
    build_id: str | None = None,
) -> dict[str, Any] | None:
    await ensure_db_schema()
    async with async_session() as db:
        q = select(ReleaseEvent).order_by(ReleaseEvent.updated_at.desc()).limit(1)
        if build_id:
            q = select(ReleaseEvent).where(ReleaseEvent.build_id == build_id).limit(1)
        elif pipeline_id and pr_id:
            q = (
                select(ReleaseEvent)
                .where(
                    ReleaseEvent.pipeline_id == pipeline_id,
                    ReleaseEvent.pr_id == pr_id,
                )
                .order_by(ReleaseEvent.updated_at.desc())
                .limit(1)
            )
        elif pr_id:
            q = (
                select(ReleaseEvent)
                .where(ReleaseEvent.pr_id == pr_id)
                .order_by(ReleaseEvent.updated_at.desc())
                .limit(1)
            )
        elif pipeline_id:
            q = (
                select(ReleaseEvent)
                .where(ReleaseEvent.pipeline_id == pipeline_id)
                .order_by(ReleaseEvent.updated_at.desc())
                .limit(1)
            )
        row = (await db.execute(q)).scalars().first()
        return to_dict(row) if row else None


async def list_recent_releases(limit: int = 10) -> list[dict[str, Any]]:
    await ensure_db_schema()
    async with async_session() as db:
        rows = (
            await db.execute(
                select(ReleaseEvent).order_by(ReleaseEvent.updated_at.desc()).limit(limit)
            )
        ).scalars().all()
        return [to_dict(r) for r in rows]
