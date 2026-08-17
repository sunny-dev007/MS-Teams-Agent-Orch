import pytest

from agent.planner.agent import is_simple_greeting, plan
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
    assert "Email Agent" in HELP_MENU
    assert "Document Knowledge Fabric" in HELP_MENU
    assert "[SP]=SharePoint" in HELP_MENU
    assert "check my repos" in HELP_MENU
    assert "ask docs" in HELP_MENU
    assert "write release notes" in HELP_MENU
    assert "run QA" in HELP_MENU


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
    assert "Personal AI Agent" in result["notification_text"]


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
