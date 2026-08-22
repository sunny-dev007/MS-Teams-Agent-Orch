"""Copilot / Teams channel API tests — WhatsApp paths must stay green separately."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.main import app
from agent.workflow.gates import GATE_PLAN


@pytest.fixture()
def copilot_env(monkeypatch):
    monkeypatch.setenv("ENABLE_TEAMS_COPILOT_CHANNEL", "true")
    monkeypatch.setenv("COPILOT_API_KEY", "test-copilot-key")
    monkeypatch.setenv("ALLOWED_TEAMS_USER_IDS", "user-oid-1")
    # Force settings reload is hard; patch settings object instead
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_teams_copilot_channel", True)
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "copilot_api_key", SecretStr("test-copilot-key"))
    monkeypatch.setattr(settings, "allowed_teams_user_ids", ["user-oid-1"])
    yield


@pytest.mark.asyncio
async def test_copilot_rejects_missing_api_key(copilot_env):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/channels/copilot/message",
            json={"user_id": "user-oid-1", "message": "hello"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_copilot_rejects_unknown_user(copilot_env):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/channels/copilot/message",
            json={"user_id": "other-user", "message": "hello"},
            headers={"X-Copilot-Api-Key": "test-copilot-key"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_copilot_message_routes_and_returns_outbox(copilot_env):
    session = {
        "phone": "teams:user-oid-1",
        "awaiting": GATE_PLAN,
        "provider": "azure_devops",
        "data": {"pending_task_id": "abc12345", "channel": "teams"},
    }

    async def fake_route(session_id, message, *, schedule, graph_payload=None, source="whatsapp"):
        # Simulate gate hint scheduled like WhatsApp
        async def hint():
            from agent.services.channel_notify import send_channel_message

            await send_channel_message(session_id, "Plan reminder for Teams")

        schedule(hint)

    with (
        patch("agent.api.channel_gates.route_inbound_message", new=fake_route),
        patch("agent.core.session.get_session", new_callable=AsyncMock, return_value=session),
        patch("agent.core.session.save_session", new_callable=AsyncMock),
        patch(
            "agent.core.channel_outbox.drain_outbox",
            new_callable=AsyncMock,
            return_value=["Plan reminder for Teams"],
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/channels/copilot/message",
                json={"user_id": "user-oid-1", "message": "status"},
                headers={"X-Copilot-Api-Key": "test-copilot-key"},
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["channel"] == "teams"
    assert data["session_id"] == "teams:user-oid-1"
    assert "Plan reminder" in data["reply"] or data["pending_updates"]


@pytest.mark.asyncio
async def test_copilot_health(copilot_env, monkeypatch):
    from agent.config import settings
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "enable_docs_agent", False)
    monkeypatch.setattr(settings, "enable_qa_agent", False)
    monkeypatch.setattr(settings, "enable_doc_knowledge", False)
    monkeypatch.setattr(settings, "enable_qdrant", True)
    monkeypatch.setattr(settings, "qdrant_url", "")
    monkeypatch.setattr(settings, "qdrant_api_key", SecretStr(""))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/channels/copilot/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["api_key_configured"] is True
    assert body["allowlist_size"] == 1
    assert "azdo_configured" in body
    assert "github_configured" in body
    assert "fabric" in body
    assert body["fabric"]["docs_agent_enabled"] is False
    assert body["fabric"]["qa_agent_enabled"] is False
    assert body["fabric"]["doc_knowledge_enabled"] is False
    assert body["fabric"]["outlook_agent_enabled"] is False
    assert body["fabric"]["boards_agent_enabled"] is False
    assert "doc_knowledge_ready" in body["fabric"]
    assert "outlook_agent_ready" in body["fabric"]
    assert "boards_agent_ready" in body["fabric"]
    assert body["fabric"]["qdrant_configured"] is False
    assert "qdrant_ready" in body["fabric"]


@pytest.mark.asyncio
async def test_copilot_help_returns_adaptive_card(copilot_env):
    session = {
        "phone": "teams:user-oid-1",
        "awaiting": None,
        "data": {"channel": "teams"},
    }

    async def fake_route(session_id, message, *, schedule, graph_payload=None, source="whatsapp"):
        async def help_msg():
            from agent.core.persona import build_help_menu
            from agent.services.channel_notify import send_channel_message

            await send_channel_message(session_id, build_help_menu(channel="teams"))

        schedule(help_msg)

    with (
        patch("agent.api.channel_gates.route_inbound_message", new=fake_route),
        patch("agent.core.session.get_session", new_callable=AsyncMock, return_value=session),
        patch("agent.core.session.save_session", new_callable=AsyncMock),
        patch(
            "agent.core.channel_outbox.drain_outbox",
            new_callable=AsyncMock,
            return_value=["Command catalog"],
        ),
        patch(
            "agent.core.channel_outbox.peek_outbox_count",
            new_callable=AsyncMock,
            return_value=0,
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/channels/copilot/message",
                json={"user_id": "user-oid-1", "message": "help"},
                headers={"X-Copilot-Api-Key": "test-copilot-key"},
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("adaptive_card", {}).get("type") == "AdaptiveCard"
    assert "FactSet" in str(data["adaptive_card"])


@pytest.mark.asyncio
async def test_copilot_status_drains_outbox_before_gate(copilot_env):
    """Status must return completed work from outbox, not only gate hints."""
    session = {
        "phone": "teams:user-oid-1",
        "awaiting": GATE_PLAN,
        "provider": "azure_devops",
        "data": {
            "pending_task_id": "abc12345",
            "channel": "teams",
            "user_message": {"original": "release note for PR 51"},
        },
    }

    with (
        patch("agent.core.session.get_session", new_callable=AsyncMock, return_value=session),
        patch("agent.core.session.save_session", new_callable=AsyncMock),
        patch(
            "agent.core.channel_outbox.drain_outbox",
            new_callable=AsyncMock,
            return_value=["*Documentation Agent* — enterprise release notes published."],
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/channels/copilot/message",
                json={"user_id": "user-oid-1", "message": "Status"},
                headers={"X-Copilot-Api-Key": "test-copilot-key"},
            )
    assert resp.status_code == 200
    data = resp.json()
    assert "release notes published" in data["reply"].lower()
    assert "Gate 1" not in data["reply"]


@pytest.mark.asyncio
async def test_copilot_status_tolerates_corrupted_session_user_message(copilot_env):
    """Regression: non-string user_message must not surface temporary error on Status."""
    session = {
        "phone": "teams:user-oid-1",
        "awaiting": GATE_PLAN,
        "provider": "azure_devops",
        "data": {
            "pending_task_id": "abc12345",
            "channel": "teams",
            "user_message": {"nested": "bad"},
        },
    }

    with (
        patch("agent.core.session.get_session", new_callable=AsyncMock, return_value=session),
        patch("agent.core.session.save_session", new_callable=AsyncMock),
        patch("agent.core.channel_outbox.drain_outbox", new_callable=AsyncMock, return_value=[]),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/channels/copilot/message",
                json={"user_id": "user-oid-1", "message": "Status"},
                headers={"X-Copilot-Api-Key": "test-copilot-key"},
            )
    assert resp.status_code == 200
    assert "temporary error" not in resp.json()["reply"].lower()
    assert "PROCEED abc12345" in resp.json()["reply"]


@pytest.mark.asyncio
async def test_copilot_message_soft_fails_instead_of_500(copilot_env):
    """Handler exceptions must return HTTP 200 with an error reply (Studio tool UX)."""
    with (
        patch(
            "agent.core.session.save_session",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated outbox/session failure"),
        ),
        patch(
            "agent.api.channel_gates.route_inbound_message",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/channels/copilot/message",
                json={"user_id": "user-oid-1", "message": "check my repos"},
                headers={"X-Copilot-Api-Key": "test-copilot-key"},
            )
    assert resp.status_code == 200
    assert "temporary error" in resp.json()["reply"].lower()
