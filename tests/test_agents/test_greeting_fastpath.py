import pytest

from agent.planner.agent import is_simple_greeting, plan
from agent.core.persona import (
    GREETING_REPLY,
    HELP_MENU,
    build_greeting_reply,
    build_help_menu,
)


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
    help_text = build_help_menu()
    greet = build_greeting_reply()
    assert "Active agents" in help_text
    assert "Active agents" in greet
    assert "Command catalog" in help_text
    assert "[SP]" in help_text
    assert "Dev Agent" in greet
    assert "check my repos" in help_text
    assert "ask docs" in help_text
    assert "write release notes" in help_text
    assert "run QA" in help_text
    # Legacy constants still export usable text
    assert "Sunny" in HELP_MENU


def test_greeting_respects_session_gate():
    text = build_greeting_reply(session={"awaiting": "plan_approval"})
    assert "plan_approval" in text
    assert "status" in text.lower()


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
async def test_planner_help_fastpath():
    result = await plan({"user_message": "help", "task_id": "t1", "whatsapp_phone": ""})
    assert result["intent"] == "general"
    assert result["planned_by"] == "planner"
    assert "Command catalog" in result["notification_text"]
    assert "Active agents" in result["notification_text"]


@pytest.mark.asyncio
async def test_planner_hello_shows_agents():
    result = await plan({"user_message": "Hello", "task_id": "t1", "whatsapp_phone": ""})
    assert result["intent"] == "general"
    assert "Active agents" in result["notification_text"]
    assert "Suggested next" in result["notification_text"]


@pytest.mark.asyncio
async def test_planner_emails_fastpath():
    result = await plan({"user_message": "check my emails", "task_id": "t1", "whatsapp_phone": ""})
    assert result["intent"] == "check_email"
    assert result["planned_by"] == "planner"


@pytest.mark.asyncio
async def test_graph_uses_planner_entry():
    from agent.agents.graph import build_graph

    g = build_graph()
    assert "planner" in g.nodes
    assert "email_agent" in g.nodes
    assert "github_agent" not in g.nodes  # github is used via repo_wizard, not a top-level node
    assert "repo_wizard" in g.nodes
    assert "coding_developer" in g.nodes
