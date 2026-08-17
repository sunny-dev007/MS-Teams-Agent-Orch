import pytest

from agent.planner.agent import is_simple_greeting, plan
from agent.core.persona import (
    GREETING_REPLY,
    HELP_MENU,
    build_greeting_adaptive_card,
    build_greeting_reply,
    build_help_adaptive_card,
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
    help_wa = build_help_menu(channel="whatsapp")
    help_teams = build_help_menu(channel="teams")
    greet_teams = build_greeting_reply(channel="teams")
    assert "Active agents" in help_wa
    assert "Command catalog" in help_teams
    assert "| Agent | Status |" in help_teams
    assert "**check my repos**" in help_teams
    assert "`ON`" not in help_teams  # no code-pill clutter on Teams
    assert "Suggested next" in greet_teams
    assert "Sunny" in HELP_MENU


def test_teams_adaptive_cards():
    help_card = build_help_adaptive_card()
    assert help_card["type"] == "AdaptiveCard"
    assert any(b.get("type") == "FactSet" for b in help_card["body"])
    greet_card = build_greeting_adaptive_card(session={"awaiting": None})
    assert greet_card["type"] == "AdaptiveCard"


def test_greeting_respects_session_gate():
    text = build_greeting_reply(
        session={"awaiting": "plan_approval"}, channel="teams"
    )
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
async def test_planner_help_fastpath_teams():
    result = await plan(
        {"user_message": "help", "task_id": "t1", "whatsapp_phone": "teams:u1"}
    )
    assert result["intent"] == "general"
    assert "| Agent | Status |" in result["notification_text"]
    assert "**help**" in result["notification_text"]


@pytest.mark.asyncio
async def test_planner_hello_shows_agents():
    result = await plan(
        {"user_message": "Hello", "task_id": "t1", "whatsapp_phone": "teams:u1"}
    )
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
    assert "github_agent" not in g.nodes
    assert "repo_wizard" in g.nodes
    assert "coding_developer" in g.nodes
