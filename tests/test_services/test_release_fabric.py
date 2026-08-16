"""Release Event store + fabric flag safety."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.models import release_event as re_mod
from agent.models.db import Base


@pytest.fixture()
async def release_db(tmp_path, monkeypatch):
    db_path = tmp_path / "release.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _noop():
        return None

    monkeypatch.setattr(re_mod, "async_session", session_factory)
    monkeypatch.setattr(re_mod, "ensure_db_schema", _noop)
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_and_find_release_event(release_db):
    created = await re_mod.upsert_release_event(
        pr_id="42",
        pipeline_id="15",
        build_id="200",
        status=re_mod.STATUS_DEPLOYED,
        app_url="https://example.com",
        title="Demo",
    )
    assert created["release_id"].startswith("rel_")
    found = await re_mod.find_release_event(pr_id="42", pipeline_id="15")
    assert found is not None
    assert found["build_id"] == "200"
    updated = await re_mod.upsert_release_event(
        release_id=created["release_id"],
        status=re_mod.STATUS_DOCS_PUBLISHED,
        doc_url="https://contoso.sharepoint.com/sites/x",
    )
    assert updated["status"] == re_mod.STATUS_DOCS_PUBLISHED
    assert updated["doc_url"]


@pytest.mark.asyncio
async def test_docs_agent_disabled_message(monkeypatch):
    from agent.agents.docs_agent import publish_release_notes
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_docs_agent", False)
    out = await publish_release_notes(
        {"user_message": "write release notes for PR 1", "whatsapp_phone": "teams:u"}
    )
    assert out["status"] == "skipped"
    assert "disabled" in (out.get("notification_text") or "").lower()


@pytest.mark.asyncio
async def test_qa_agent_disabled_message(monkeypatch):
    from agent.agents.qa_agent import run_qa_smoke
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_qa_agent", False)
    out = await run_qa_smoke({"user_message": "run QA for release", "whatsapp_phone": "teams:u"})
    assert out["status"] == "skipped"
    assert "disabled" in (out.get("notification_text") or "").lower()


@pytest.mark.asyncio
async def test_handoff_noop_when_flag_off(monkeypatch):
    from agent.config import settings
    from agent.services.release_handoff import emit_release_handoff

    monkeypatch.setattr(settings, "enable_release_handoff", False)
    assert await emit_release_handoff(phone="teams:u", pr_id="1") is None


@pytest.mark.asyncio
async def test_planner_routes_release_notes():
    from agent.planner.agent import plan

    out = await plan({"user_message": "write release notes for PR 55", "whatsapp_phone": ""})
    assert out["intent"] == "publish_release_notes"


@pytest.mark.asyncio
async def test_planner_release_notes_beats_repo_session(monkeypatch):
    """Mid repo-wizard session must not swallow SharePoint docs intent."""
    from agent.planner import agent as planner

    async def fake_session(phone):
        return {"awaiting": "repo_pick", "provider": "azure_devops", "data": {}}

    async def fake_save(*args, **kwargs):
        return None

    monkeypatch.setattr(planner, "get_session", fake_session)
    monkeypatch.setattr("agent.core.session.save_session", fake_save)
    out = await planner.plan(
        {
            "user_message": "write release notes for PR 42",
            "whatsapp_phone": "teams:user-1",
        }
    )
    assert out["intent"] == "publish_release_notes"


@pytest.mark.asyncio
async def test_planner_routes_run_qa():
    from agent.planner.agent import plan

    out = await plan({"user_message": "run QA for this release", "whatsapp_phone": ""})
    assert out["intent"] == "run_qa"


@pytest.mark.asyncio
async def test_fabric_flags_default_off():
    from agent.config import Settings

    s = Settings(
        _env_file=None,
        enable_docs_agent=False,
        enable_qa_agent=False,
        enable_release_handoff=False,
    )
    assert s.enable_docs_agent is False
    assert s.enable_qa_agent is False
    assert s.enable_release_handoff is False
