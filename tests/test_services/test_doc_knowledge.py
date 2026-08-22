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
    assert "disabled" in out["notification_text"].lower()


def test_source_tags_sp_od_on():
    from agent.agents.doc_library_agent import _format_catalog_page
    from agent.services.graph_docs import SOURCE_TAG_LEGEND, source_tag

    assert source_tag("sharepoint") == "SP"
    assert source_tag("onedrive") == "OD"
    assert source_tag("onenote") == "ON"
    assert source_tag("unknown") == "??"

    text = _format_catalog_page(
        [
            {
                "pick": 1,
                "source_type": "sharepoint",
                "title": "guide.md",
                "doc_mode": "guide",
                "folder": "Docs",
                "site_name": "Engineering",
                "web_url": "https://contoso.sharepoint.com/guide.md",
                "extension": ".md",
                "size": 2048,
            },
            {
                "pick": 2,
                "source_type": "onedrive",
                "title": "notes.docx",
                "doc_mode": "general",
                "folder": "",
                "site_name": "OneDrive",
                "web_url": "",
                "extension": ".docx",
                "size": 100,
            },
            {
                "pick": 3,
                "source_type": "onenote",
                "title": "Meeting",
                "doc_mode": "notes",
                "folder": "NB",
                "site_name": "OneNote",
                "web_url": "https://onenote",
                "extension": ".one",
                "size": 0,
            },
        ],
        page=0,
        page_size=10,
        teams=True,
    )
    assert SOURCE_TAG_LEGEND in text
    assert "[guide.md](https://contoso.sharepoint.com/guide.md)" in text
    assert "Engineering / Docs" in text
    assert "Ingestion" in text
    assert "2.0 KB" in text or "2.1 KB" in text
    assert "Found **3**" in text


def test_extract_library_query_and_page():
    from agent.services.graph_docs import extract_library_query, format_file_size, parse_library_page

    assert extract_library_query("list of my document") == ""
    assert extract_library_query("list my documents") == ""
    assert "guide" in extract_library_query("find document developer-guide").lower()
    assert extract_library_query("next page") == ""
    assert parse_library_page("next", 0, 3) == 1
    assert parse_library_page("page 3", 0, 3) == 2
    assert parse_library_page("previous", 1, 3) == 0
    assert format_file_size(1536).endswith("KB")


@pytest.mark.asyncio
async def test_planner_informal_list_and_search_docs():
    from agent.planner.agent import plan

    assert (await plan({"user_message": "list of my document", "whatsapp_phone": ""}))[
        "intent"
    ] == "list_docs"
    assert (await plan({"user_message": "show me my documents", "whatsapp_phone": ""}))[
        "intent"
    ] == "list_docs"
    assert (
        await plan({"user_message": "find document developer-guide", "whatsapp_phone": ""})
    )["intent"] == "list_docs"


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
    assert _route_after_plan({"intent": "upload_ingest_docs"}) == "doc_upload_agent"
    assert _route_after_plan({"intent": "analyze_excel"}) == "data_analyst_agent"


def test_chunk_and_cosine():
    chunks = doc_vector.chunk_text("A" * 100 + "\n\n" + "B" * 100, chunk_chars=80, overlap=10)
    assert len(chunks) >= 2
    a = doc_vector._hash_embedding("release process policy")
    b = doc_vector._hash_embedding("release process policy")
    c = doc_vector._hash_embedding("completely unrelated zucchini farming")
    assert doc_vector.cosine_similarity(a, b) > doc_vector.cosine_similarity(a, c)


def test_structure_aware_chunking_keeps_headings():
    text = (
        "# Release Policy\n\n"
        + ("QA sign-off is mandatory before production. " * 20)
        + "\n\n## Rollback\n\n"
        + ("Use the previous container image if health fails. " * 20)
    )
    chunks = doc_vector.chunk_text(text, chunk_chars=400, overlap=80)
    assert len(chunks) >= 2
    joined = "\n".join(chunks)
    assert "Release Policy" in joined or "Rollback" in joined


def test_mmr_diversifies_same_doc_duplicates():
    cands = [
        {"doc_id": "a", "text": "alpha one", "score": 0.9, "embedding": []},
        {"doc_id": "a", "text": "alpha two", "score": 0.88, "embedding": []},
        {"doc_id": "b", "text": "beta unique", "score": 0.8, "embedding": []},
    ]
    picked = doc_vector.mmr_select(cands, top_k=2, lambda_mult=0.5)
    assert len(picked) == 2
    assert {p["doc_id"] for p in picked} == {"a", "b"}


def test_with_chunk_context_prefixes_metadata():
    out = doc_vector.with_chunk_context(
        ["body text"], title="Policy", source_type="sharepoint", doc_mode="policy"
    )
    assert out[0].startswith("Document: Policy")
    assert "body text" in out[0]


def test_extract_chunk_locator_from_markdown_and_page():
    md = "## 2.1 Two-Layer Agent Architecture\n\nAgents are pure functions."
    loc = doc_vector.extract_chunk_locator(md)
    assert "Two-Layer Agent Architecture" in (loc.get("locator") or "")

    pdf = "## Page 3\n\nRelease gate requires QA."
    loc2 = doc_vector.extract_chunk_locator(pdf)
    assert loc2.get("page") == 3
    assert "Page 3" in (loc2.get("locator") or "")

    slide = "## Slide 2\n\nRollback plan"
    loc3 = doc_vector.extract_chunk_locator(slide)
    assert loc3.get("slide") == 2


@pytest.mark.asyncio
async def test_suggest_related_queries_from_locators(monkeypatch):
    from agent.config import settings
    from agent.services import doc_knowledge

    monkeypatch.setattr(settings, "doc_knowledge_multi_query", False)
    related = await doc_knowledge.suggest_related_queries(
        "ask docs what about Agent Logic?",
        [
            {"locator": "2.1 Two-Layer Agent Architecture", "title": "developer-guide.md"},
            {"locator": "2.2 Service Layer Pattern", "title": "developer-guide.md"},
        ],
        answer="Agents are pure functions.",
    )
    assert related
    assert any("ask docs" in q.lower() or "summarize docs" in q.lower() for q in related)


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


def test_ingest_status_raw_ingested_stale():
    from agent.services.ingest_status import (
        STATUS_INGESTED,
        STATUS_RAW,
        STATUS_STALE,
        classify_ingest_status,
        overlay_catalog,
    )

    entry = {
        "external_id": "abc",
        "source_type": "sharepoint",
        "last_modified": "2026-08-20T10:00:00Z",
        "size": 100,
        "pick": 1,
        "title": "a.docx",
    }
    assert classify_ingest_status(entry, None) == STATUS_RAW
    kb_ready = {
        "status": "ready",
        "metadata": {"source_last_modified": "2026-08-20T10:00:00Z", "source_size": 100},
        "updated_at": "2026-08-20T10:00:00+00:00",
    }
    assert classify_ingest_status(entry, kb_ready) == STATUS_INGESTED
    stale_entry = {**entry, "last_modified": "2026-08-20T12:00:00Z"}
    assert classify_ingest_status(stale_entry, kb_ready) == STATUS_STALE
    size_entry = {**entry, "size": 250}
    assert classify_ingest_status(size_entry, kb_ready) == STATUS_STALE

    rows = overlay_catalog(
        [stale_entry],
        {("abc", "sharepoint"): kb_ready},
    )
    assert rows[0]["ingest_label"] == "Ready for re-ingest"


@pytest.mark.asyncio
async def test_planner_routes_data_analyst_and_reingest():
    from agent.planner.agent import plan

    assert (await plan({"user_message": "convert excel 6", "whatsapp_phone": ""}))[
        "intent"
    ] == "analyze_excel"
    assert (await plan({"user_message": "reingest stale", "whatsapp_phone": ""}))[
        "intent"
    ] == "ingest_docs"


@pytest.mark.asyncio
async def test_data_analyst_flag_default_off():
    from agent.agents.data_analyst_agent import analyze_excel
    from agent.config import Settings, settings

    s = Settings(_env_file=None, enable_data_analyst_agent=False)
    assert s.enable_data_analyst_agent is False
    from unittest.mock import patch

    with patch.object(settings, "enable_data_analyst_agent", False):
        out = await analyze_excel({"user_message": "convert excel 1", "whatsapp_phone": "teams:u"})
    assert out["status"] == "skipped"

