"""Document Knowledge Fabric — flags, routing, chunk/vector, store."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.models import knowledge_doc as kd_mod
from agent.models.db import Base
from agent.services import doc_vector


@pytest.fixture()
async def kb_db(tmp_path, monkeypatch):
    # Ensure ORM tables are registered on Base.metadata
    from agent.models import knowledge_doc as _kd  # noqa: F401

    db_path = tmp_path / "kb.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _noop():
        return None

    monkeypatch.setattr(kd_mod, "async_session", session_factory)
    monkeypatch.setattr(kd_mod, "ensure_db_schema", _noop)
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_doc_knowledge_flag_default_off():
    from agent.config import Settings

    s = Settings(_env_file=None, enable_doc_knowledge=False)
    assert s.enable_doc_knowledge is False


@pytest.mark.asyncio
async def test_library_disabled_message(monkeypatch):
    from agent.agents.doc_library_agent import list_documents
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_doc_knowledge", False)
    out = await list_documents({"user_message": "list my documents", "whatsapp_phone": "teams:u"})
    assert out["status"] == "skipped"
    assert "disabled" in (out.get("notification_text") or "").lower()


@pytest.mark.asyncio
async def test_ingest_rag_insights_disabled(monkeypatch):
    from agent.agents.doc_ingest_agent import ingest_documents
    from agent.agents.doc_insights_agent import summarize_documents
    from agent.agents.doc_rag_agent import ask_documents
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_doc_knowledge", False)
    for fn, msg in (
        (ingest_documents, "ingest 1"),
        (ask_documents, "ask docs what is X"),
        (summarize_documents, "summarize docs"),
    ):
        out = await fn({"user_message": msg, "whatsapp_phone": "teams:u"})
        assert out["status"] == "skipped"


@pytest.mark.asyncio
async def test_planner_routes_knowledge_intents():
    from agent.planner.agent import plan

    assert (await plan({"user_message": "list my documents", "whatsapp_phone": ""}))[
        "intent"
    ] == "list_docs"
    assert (await plan({"user_message": "ingest 1,2", "whatsapp_phone": ""}))[
        "intent"
    ] == "ingest_docs"
    assert (await plan({"user_message": "ask docs what is the policy?", "whatsapp_phone": ""}))[
        "intent"
    ] == "ask_docs"
    assert (await plan({"user_message": "summarize docs risks", "whatsapp_phone": ""}))[
        "intent"
    ] == "summarize_docs"


@pytest.mark.asyncio
async def test_planner_list_docs_beats_repo_session(monkeypatch):
    from agent.planner import agent as planner

    async def fake_session(phone):
        return {"awaiting": "repo_pick", "provider": "azure_devops", "data": {}}

    async def fake_save(*args, **kwargs):
        return None

    monkeypatch.setattr(planner, "get_session", fake_session)
    monkeypatch.setattr("agent.core.session.save_session", fake_save)
    out = await planner.plan(
        {"user_message": "list my documents", "whatsapp_phone": "teams:user-1"}
    )
    assert out["intent"] == "list_docs"


@pytest.mark.asyncio
async def test_graph_routes_knowledge_intents():
    from agent.agents.graph import _route_after_plan

    assert _route_after_plan({"intent": "list_docs"}) == "doc_library_agent"
    assert _route_after_plan({"intent": "ingest_docs"}) == "doc_ingest_agent"
    assert _route_after_plan({"intent": "ask_docs"}) == "doc_rag_agent"
    assert _route_after_plan({"intent": "summarize_docs"}) == "doc_insights_agent"


def test_chunk_and_cosine():
    chunks = doc_vector.chunk_text("A" * 100 + "\n\n" + "B" * 100, chunk_chars=80, overlap=10)
    assert len(chunks) >= 2
    a = doc_vector._hash_embedding("release process policy")
    b = doc_vector._hash_embedding("release process policy")
    c = doc_vector._hash_embedding("completely unrelated zucchini farming")
    assert doc_vector.cosine_similarity(a, b) > doc_vector.cosine_similarity(a, c)


@pytest.mark.asyncio
async def test_upsert_and_search(kb_db, monkeypatch):
    monkeypatch.setattr(
        "agent.services.doc_vector.embed_texts",
        AsyncMock(side_effect=lambda texts: [doc_vector._hash_embedding(t) for t in texts]),
    )
    doc = await kd_mod.upsert_document(
        external_id="ext-1",
        source_type="sharepoint",
        title="Release Policy",
        doc_mode="policy",
        status=kd_mod.STATUS_INGESTING,
        owner_session="teams:u",
    )
    records = await doc_vector.build_chunk_records(
        "Our release process requires QA sign-off before production deploy."
    )
    await kd_mod.replace_chunks(doc["id"], records)
    ready = await kd_mod.list_ready_documents(limit=10)
    assert any(d["id"] == doc["id"] for d in ready)

    with patch(
        "agent.services.doc_knowledge.invoke_llm",
        new=AsyncMock(return_value=type("R", (), {"content": "QA sign-off is required [1]."})()),
    ):
        from agent.services import doc_knowledge

        ans = await doc_knowledge.retrieve_answer(
            "What is required before production?",
            owner_session="teams:u",
        )
    assert "QA" in (ans.get("answer") or "")
    assert ans.get("citations")


@pytest.mark.asyncio
async def test_infer_doc_mode():
    from agent.services.graph_docs import infer_doc_mode

    assert infer_doc_mode("Security-Policy.pdf") == "policy"
    assert infer_doc_mode("How-To-Deploy.md") == "howto"
    assert infer_doc_mode("ReleaseNotes-41.html") == "release"
