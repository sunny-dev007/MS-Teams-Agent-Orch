import pytest

from agent.agents.router import is_simple_greeting
from agent.core.persona import GREETING_REPLY, HELP_MENU


@pytest.mark.parametrize(
    "message,expected",
    [
        ("hello", True),
        ("Hello!", True),
        ("Hi", True),
        ("hey there", False),
        ("good morning", True),
        ("please fix the bug", False),
        ("", False),
    ],
)
def test_is_simple_greeting(message, expected):
    assert is_simple_greeting(message) is expected


def test_persona_assets():
    assert "Sunny" in GREETING_REPLY
    assert "Emails" in HELP_MENU


@pytest.mark.asyncio
async def test_health_deep_includes_whatsapp(client, monkeypatch):
    async def fake_check():
        return {"ok": False, "error": "token_rejected"}

    monkeypatch.setattr(
        "agent.services.whatsapp.check_access_token",
        fake_check,
    )
    resp = await client.get("/health", params={"deep": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["whatsapp"]["error"] == "token_rejected"


@pytest.mark.asyncio
async def test_router_help_fastpath():
    from agent.agents.router import route_input

    result = await route_input({"user_message": "help", "task_id": "t1", "whatsapp_phone": ""})
    assert result["intent"] == "general"
    assert "Personal AI Agent" in result["notification_text"]


@pytest.mark.asyncio
async def test_router_emails_fastpath():
    from agent.agents.router import route_input

    result = await route_input({"user_message": "check my emails", "task_id": "t1", "whatsapp_phone": ""})
    assert result["intent"] == "check_email"
