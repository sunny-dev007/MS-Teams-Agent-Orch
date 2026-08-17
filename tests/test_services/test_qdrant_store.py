"""Tests for Qdrant vector store integration (mocked HTTP)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from agent.services import qdrant_store


@pytest.fixture()
def qdrant_settings(monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_qdrant", True)
    monkeypatch.setattr(settings, "enable_doc_knowledge", True)
    monkeypatch.setattr(settings, "qdrant_url", "https://example.cloud.qdrant.io:6333")
    monkeypatch.setattr(settings, "qdrant_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "qdrant_collection", "doc_knowledge_chunks")
    qdrant_store._COLLECTION_ENSURED.clear()
    yield
    qdrant_store._COLLECTION_ENSURED.clear()


def test_qdrant_configured_requires_url_and_key(monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_qdrant", True)
    monkeypatch.setattr(settings, "qdrant_url", "")
    monkeypatch.setattr(settings, "qdrant_api_key", SecretStr(""))
    assert qdrant_store.qdrant_configured() is False


def test_point_id_stable():
    a = qdrant_store.point_id("kd_abc", 0)
    b = qdrant_store.point_id("kd_abc", 0)
    c = qdrant_store.point_id("kd_abc", 1)
    assert a == b
    assert a != c


@pytest.mark.asyncio
async def test_upsert_and_search(qdrant_settings):
    calls: list[tuple[str, str]] = []

    async def fake_request(method, path, *, json_body=None, timeout=60.0):
        calls.append((method, path))
        if method == "GET" and path.startswith("/collections/"):
            # Force create path
            import httpx

            resp = httpx.Response(404, request=httpx.Request(method, "http://x"))
            raise httpx.HTTPStatusError("missing", request=resp.request, response=resp)
        if method == "PUT" and path.endswith("/doc_knowledge_chunks"):
            return {"result": True}
        if method == "PUT" and path.endswith("/index"):
            return {"result": True}
        if method == "POST" and path.endswith("/points/delete"):
            return {"result": True}
        if method == "PUT" and "points" in path:
            return {"result": {"status": "ok"}}
        if method == "POST" and path.endswith("/points/search"):
            return {
                "result": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "score": 0.91,
                        "payload": {
                            "doc_id": "kd_1",
                            "chunk_index": 0,
                            "title": "Policy",
                            "source_type": "sharepoint",
                            "doc_mode": "policy",
                            "web_url": "https://example",
                            "text": "QA sign-off required",
                        },
                    }
                ]
            }
        return {}

    with patch.object(qdrant_store, "_request", side_effect=fake_request):
        n = await qdrant_store.upsert_document_chunks(
            doc_id="kd_1",
            title="Policy",
            source_type="sharepoint",
            doc_mode="policy",
            web_url="https://example",
            owner_session="teams:u",
            records=[{"text": "QA sign-off required", "embedding": [0.1, 0.2, 0.3]}],
        )
        assert n == 1
        hits = await qdrant_store.search([0.1, 0.2, 0.3], top_k=3)
        assert hits and hits[0]["title"] == "Policy"
        assert hits[0]["score"] == 0.91


@pytest.mark.asyncio
async def test_ingest_uses_qdrant_when_configured(monkeypatch, qdrant_settings):
    from agent.services import doc_knowledge

    monkeypatch.setattr(
        "agent.services.doc_vector.build_chunk_records",
        AsyncMock(
            return_value=[{"text": "hello world", "embedding": [0.2, 0.3, 0.4]}]
        ),
    )
    monkeypatch.setattr(
        "agent.services.graph_docs.fetch_document_text",
        AsyncMock(return_value=("hello world", "text_extracted")),
    )
    monkeypatch.setattr(
        "agent.models.knowledge_doc.upsert_document",
        AsyncMock(
            side_effect=lambda **kw: {
                "id": kw.get("doc_id") or "kd_x",
                "doc_mode": kw.get("doc_mode") or "general",
                **{k: kw.get(k) for k in ("title", "status", "chunk_count")},
            }
        ),
    )
    monkeypatch.setattr(
        "agent.models.knowledge_doc.replace_chunks",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        qdrant_store,
        "upsert_document_chunks",
        AsyncMock(return_value=1),
    )

    results = await doc_knowledge.ingest_catalog_entries(
        [
            {
                "external_id": "e1",
                "source_type": "sharepoint",
                "title": "Doc A",
                "web_url": "https://x",
                "mime_type": "text/plain",
                "doc_mode": "general",
            }
        ],
        owner_session="teams:u",
    )
    assert results[0]["status"] == "ready"
    assert results[0]["vector_backend"] == "qdrant"
