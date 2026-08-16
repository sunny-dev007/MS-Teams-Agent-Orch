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
async def test_copilot_health(copilot_env):
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
