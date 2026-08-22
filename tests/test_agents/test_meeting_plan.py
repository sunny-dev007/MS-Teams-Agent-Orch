"""Tests for Meeting Intelligence agents."""

import pytest


@pytest.mark.asyncio
async def test_plan_agent_disabled_flag(monkeypatch):
    from agent.agents.meeting_plan_agent import run_meeting_plan
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", False)
    out = await run_meeting_plan({"user_message": "make a plan", "whatsapp_phone": "teams:u"})
    assert out["status"] == "skipped"


@pytest.mark.asyncio
async def test_library_select_invalid_pick(monkeypatch):
    from agent.agents import meeting_library_agent as lib
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", True)

    async def fake_graph_configured():
        return True

    async def fake_session(phone):
        return {
            "awaiting": "meeting_pick",
            "data": {"meeting_catalog": [{"pick": 1, "title": "T.vtt", "client_name": "X"}]},
        }

    monkeypatch.setattr(lib.ms_graph, "graph_configured", lambda: True)
    monkeypatch.setattr(lib, "get_session", fake_session)
    out = await lib.run_meeting_library(
        {
            "user_message": "select meetings 9",
            "whatsapp_phone": "teams:u",
            "intent": "select_meetings",
        }
    )
    assert out["status"] == "failed"
    assert "could not match" in out["notification_text"].lower()


@pytest.mark.asyncio
async def test_email_requires_recipients(monkeypatch):
    from agent.agents import meeting_email_agent as mail
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", True)

    async def fake_session(phone):
        return {
            "awaiting": "meeting_pick",
            "data": {
                "last_plan": {
                    "title": "Plan",
                    "md_url": "https://x/plan.md",
                    "plan_json": {"executive_summary": "Hi"},
                }
            },
        }

    saved = {}

    async def fake_save(phone, **kwargs):
        saved.update(kwargs)
        return None

    monkeypatch.setattr(mail, "get_session", fake_session)
    monkeypatch.setattr(mail, "save_session", fake_save)
    out = await mail.run_meeting_email(
        {"user_message": "email the plan", "whatsapp_phone": "teams:u"}
    )
    assert out["session_awaiting"] == "meeting_email_to"
    assert saved.get("awaiting") == "meeting_email_to"


@pytest.mark.asyncio
async def test_planner_list_meetings_beats_repo_session(monkeypatch):
    from agent.planner import agent as planner
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", True)

    async def fake_session(phone):
        return {"awaiting": "repo_pick", "provider": "azure_devops", "data": {}}

    async def fake_save(*args, **kwargs):
        return None

    monkeypatch.setattr(planner, "get_session", fake_session)
    monkeypatch.setattr("agent.core.session.save_session", fake_save)
    out = await planner.plan(
        {"user_message": "list my recent meetings", "whatsapp_phone": "teams:u"}
    )
    assert out["intent"] == "list_meetings"
