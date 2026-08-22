"""Doc Upload Agent — Teams attachments → SharePoint → ingest."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest


def test_normalize_attachment_base64():
    from agent.services.doc_upload import normalize_attachment

    payload = base64.b64encode(b"%PDF-1.4 hello").decode()
    att = normalize_attachment({"filename": "report.pdf", "content_base64": payload})
    assert att is not None
    assert att["filename"] == "report.pdf"
    assert att["content"].startswith(b"%PDF")
    assert att["extension"] == ".pdf"


def test_validate_attachment_rejects_unknown():
    from agent.services.doc_upload import validate_attachment

    ok, reason = validate_attachment(
        {"filename": "malware.exe", "content": b"x" * 32, "extension": ".exe"}
    )
    assert ok is False
    assert "not supported" in reason.lower()


def test_attachments_session_roundtrip():
    from agent.services.doc_upload import attachments_for_session, attachments_from_session

    raw = {"filename": "guide.pdf", "content": b"%PDF-1.4 test", "extension": ".pdf"}
    stored = attachments_for_session([raw])
    assert stored[0]["content_base64"]
    assert "content" not in stored[0]
    restored = attachments_from_session(stored)
    assert restored[0]["content"] == raw["content"]


def test_wants_upload_action_phrases():
    from agent.services.doc_upload import wants_upload_action

    assert wants_upload_action("Please ingest this attached pdf file into the system")
    assert wants_upload_action("please ingest into the system and summarise it")
    assert wants_upload_action("upload to sharepoint")
    assert wants_upload_action("process this document")
    assert not wants_upload_action("here is the file")
    assert not wants_upload_action("thanks for the update")
    assert not wants_upload_action("ingest 1,3")
    assert not wants_upload_action("ingest all")


def test_message_expects_teams_attachment():
    from agent.services.doc_upload import message_expects_teams_attachment

    assert message_expects_teams_attachment("ingest this")
    assert message_expects_teams_attachment("summarize this")
    assert message_expects_teams_attachment("please ingest into the system and summarise it")
    assert not message_expects_teams_attachment("summarize docs risks")
    assert not message_expects_teams_attachment("ingest 1,3")
    assert not message_expects_teams_attachment("list my documents")


@pytest.mark.asyncio
async def test_planner_blocks_summarize_this_without_attachment(monkeypatch):
    from agent.config import settings
    from agent.planner.agent import plan

    async def _empty_session(_phone):
        return {"phone": _phone, "awaiting": None, "provider": None, "data": {}}

    monkeypatch.setattr("agent.core.session.get_session", _empty_session)
    monkeypatch.setattr(settings, "enable_doc_knowledge", True)
    out = await plan(
        {
            "user_message": "summarize this",
            "whatsapp_phone": "teams:user-no-att",
        }
    )
    assert out["intent"] == "general"
    assert "no file bytes" in out["notification_text"].lower()


@pytest.mark.asyncio
async def test_planner_blocks_ingest_this_without_attachment(monkeypatch):
    from agent.config import settings
    from agent.planner.agent import plan

    async def _empty_session(_phone):
        return {"phone": _phone, "awaiting": None, "provider": None, "data": {}}

    monkeypatch.setattr("agent.core.session.get_session", _empty_session)
    monkeypatch.setattr(settings, "enable_doc_knowledge", True)
    out = await plan(
        {
            "user_message": "ingest this",
            "whatsapp_phone": "teams:user-no-att",
        }
    )
    assert out["intent"] == "general"
    assert "no file bytes" in out["notification_text"].lower()


@pytest.mark.asyncio
async def test_planner_combined_ingest_summarize_with_attachment(monkeypatch):
    from agent.config import settings
    from agent.planner.agent import plan

    monkeypatch.setattr(settings, "enable_doc_knowledge", True)
    att = {"filename": "guide.pdf", "content": b"%PDF-1.4 test", "extension": ".pdf"}
    out = await plan(
        {
            "user_message": "please ingest into the system and summarise it",
            "whatsapp_phone": "teams:user1",
            "attachments": [att],
        }
    )
    assert out["intent"] == "upload_ingest_docs"
    assert out.get("upload_summarize") is True


def test_unsupported_format_message():
    from agent.services.doc_upload import unsupported_format_message, validate_attachment

    ok, msg = validate_attachment(
        {"filename": "virus.exe", "content": b"x" * 32, "extension": ".exe"}
    )
    assert ok is False
    assert "not supported" in msg.lower()
    assert "PDF" in msg
    assert "not supported" in unsupported_format_message("bad.bin", ext=".bin").lower()


@pytest.mark.asyncio
async def test_planner_upload_ingest_with_attachment(monkeypatch):
    from agent.config import settings
    from agent.planner.agent import plan

    monkeypatch.setattr(settings, "enable_doc_knowledge", True)
    att = {"filename": "guide.pdf", "content": b"%PDF-1.4 test", "extension": ".pdf"}
    out = await plan(
        {
            "user_message": "Please ingest this attached pdf file into the system",
            "whatsapp_phone": "teams:user1",
            "attachments": [att],
        }
    )
    assert out["intent"] == "upload_ingest_docs"
    assert out["attachments"] == [att]
    assert out.get("upload_summarize") is False


@pytest.mark.asyncio
async def test_planner_upload_summarize_intent(monkeypatch):
    from agent.config import settings
    from agent.planner.agent import plan

    monkeypatch.setattr(settings, "enable_doc_knowledge", True)
    att = {"filename": "guide.pdf", "content": b"%PDF-1.4 test", "extension": ".pdf"}
    out = await plan(
        {
            "user_message": "summarize this attached pdf",
            "whatsapp_phone": "teams:user1",
            "attachments": [att],
        }
    )
    assert out["intent"] == "upload_ingest_docs"
    assert out.get("upload_summarize") is True


@pytest.mark.asyncio
async def test_planner_attachment_only_nudge(monkeypatch):
    from agent.config import settings
    from agent.planner.agent import plan

    monkeypatch.setattr(settings, "enable_doc_knowledge", True)
    att = {"filename": "guide.pdf", "content": b"%PDF-1.4 test", "extension": ".pdf"}
    out = await plan(
        {
            "user_message": "here is the file",
            "whatsapp_phone": "teams:user1",
            "attachments": [att],
        }
    )
    assert out["intent"] == "general"
    assert "ingest this" in out["notification_text"].lower()


def test_graph_routes_upload_ingest():
    from agent.agents.graph import _route_after_plan

    assert _route_after_plan({"intent": "upload_ingest_docs"}) == "doc_upload_agent"


@pytest.mark.asyncio
async def test_upload_agent_disabled(monkeypatch):
    from agent.agents.doc_upload_agent import upload_and_ingest_documents
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_doc_knowledge", False)
    out = await upload_and_ingest_documents(
        {"attachments": [{"filename": "a.pdf", "content": b"x" * 20}], "whatsapp_phone": "teams:u"}
    )
    assert out["status"] == "skipped"


@pytest.mark.asyncio
async def test_upload_and_ingest_happy_path(monkeypatch):
    from agent.config import settings
    from agent.services import doc_upload

    catalog_row = {
        "external_id": "item-1",
        "source_type": "sharepoint",
        "title": "report.pdf",
        "web_url": "https://contoso.sharepoint.com/report.pdf",
        "mime_type": "application/pdf",
        "extension": ".pdf",
        "folder": "UploadedDocs",
    }
    monkeypatch.setattr(settings, "enable_doc_knowledge", True)
    monkeypatch.setattr(
        doc_upload.graph_docs,
        "upload_site_drive_item",
        AsyncMock(return_value=catalog_row),
    )
    monkeypatch.setattr(
        doc_upload.doc_knowledge,
        "ingest_catalog_entries",
        AsyncMock(
            return_value=[
                {
                    "id": "kb-1",
                    "title": "report.pdf",
                    "status": "ready",
                    "chunks": 4,
                    "ingest_action": "created",
                    "extract_status": "pdf",
                    "vector_backend": "qdrant",
                }
            ]
        ),
    )
    monkeypatch.setattr(doc_upload.ms_graph, "graph_configured", lambda: True)

    att = {"filename": "report.pdf", "content": b"%PDF-1.4 " + b"x" * 64, "extension": ".pdf"}
    result = await doc_upload.upload_and_ingest_attachments([att], owner_session="teams:u")
    assert result["status"] == "completed"
    assert "Doc Upload Agent" in result["message"]
    assert "report.pdf" in result["message"]
