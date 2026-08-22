"""Complete Meeting Intelligence Fabric test coverage (pre-production gate)."""

from __future__ import annotations

import pytest

from agent.agents.graph import _route_after_plan
from agent.config import Settings


def test_meeting_intelligence_flag_defaults_off():
    s = Settings(_env_file=None)
    assert s.enable_meeting_intelligence is False
    assert s.meeting_dev_plans_folder.startswith("MeetingPlans")
    assert s.meeting_action_plans_folder.startswith("MeetingActionPlans")


def test_pick_project_by_number_and_name():
    from agent.services.meeting_board_bridge import pick_project, format_project_picker

    projects = [{"name": "Project-NIT"}, {"name": "Contoso-Web"}, {"name": "Demo"}]
    assert pick_project("2", projects)["name"] == "Contoso-Web"
    assert pick_project("Project-NIT", projects)["name"] == "Project-NIT"
    assert pick_project("contoso", projects)["name"] == "Contoso-Web"
    assert pick_project("99", projects) is None
    body = format_project_picker(projects, plan_title="Travel MVP")
    assert "Project-NIT" in body
    assert "Travel MVP" in body


@pytest.mark.asyncio
async def test_resolve_board_project_needs_picker_when_multiple(monkeypatch):
    from agent.services import meeting_board_bridge as bridge

    async def fake_list():
        return [{"name": "A"}, {"name": "B"}]

    monkeypatch.setattr(bridge.azure_boards, "list_org_projects", fake_list)
    picked, projects, needs = await bridge.resolve_board_project(
        user_message="",
        session_data={},
        default_project="",
    )
    assert picked is None
    assert needs is True
    assert len(projects) == 2


@pytest.mark.asyncio
async def test_resolve_board_project_auto_single(monkeypatch):
    from agent.services import meeting_board_bridge as bridge

    async def fake_list():
        return [{"name": "Only-One"}]

    monkeypatch.setattr(bridge.azure_boards, "list_org_projects", fake_list)
    picked, projects, needs = await bridge.resolve_board_project(
        user_message="",
        session_data={},
        default_project="",
    )
    assert needs is False
    assert picked["name"] == "Only-One"


@pytest.mark.asyncio
async def test_resolve_board_project_from_user_number(monkeypatch):
    from agent.services import meeting_board_bridge as bridge

    session = {"meeting_board_projects": [{"name": "Alpha"}, {"name": "Beta"}]}
    picked, _, needs = await bridge.resolve_board_project(
        user_message="2",
        session_data=session,
        default_project="",
    )
    assert needs is False
    assert picked["name"] == "Beta"


def test_heuristic_classify_development_keywords():
    from agent.services import meeting_intent as mi

    data = mi._heuristic_classify(
        "We need API architecture, Azure DevOps sprint, deploy MVP backend integration",
        client_name="Acme",
    )
    assert data["meeting_category"] == mi.MEETING_DEV
    assert data["requires_devops_board"] is True
    assert "acme" in data["sharepoint_folder_slug"]


def test_heuristic_classify_general():
    from agent.services import meeting_intent as mi

    data = mi._heuristic_classify("Weekly status and holiday schedule", client_name="Ops")
    assert data["meeting_category"] == mi.MEETING_GENERAL
    assert data["requires_devops_board"] is False


@pytest.mark.asyncio
async def test_classify_meeting_intent_mock_llm(monkeypatch):
    from agent.services import meeting_intent as mi

    class FakeResp:
        content = (
            '{"meeting_category":"business","confidence":0.9,'
            '"requires_devops_board":false,'
            '"sharepoint_folder_slug":"client-discovery",'
            '"topic_label":"Client Discovery","rationale":"Sales discovery"}'
        )

    async def fake_llm(*args, **kwargs):
        return FakeResp()

    monkeypatch.setattr(mi, "invoke_llm", fake_llm)
    data = await mi.classify_meeting_intent(
        meetings_block="Discovery for travel product pricing",
        client_name="Voyager",
        titles=["Discovery"],
    )
    assert data["meeting_category"] == "business"
    assert data["requires_devops_board"] is False
    assert data["sharepoint_folder_slug"] == "client-discovery"


def test_action_plan_markdown_and_implementation():
    from agent.services import meeting_plan_builder as builder

    action = {
        "title": "Client Action Plan",
        "executive_summary": "Summary",
        "context": "Context",
        "key_decisions": ["Decision A"],
        "action_items": [
            {"owner": "Priya", "task": "Send proposal", "due": "Fri", "priority": "High"}
        ],
        "follow_ups": ["Schedule demo"],
        "meeting_category": "business",
    }
    md = builder.plan_to_markdown(action, plan_kind="action")
    assert "# Client Action Plan" in md
    assert "Send proposal" in md
    assert "Priority" in md

    impl = {
        "title": "Impl Plan",
        "executive_summary": "Build",
        "phases": [{"name": "MVP", "duration_weeks": 4, "deliverables": ["API"]}],
        "action_items": [{"owner": "Dev", "task": "Scaffold", "due": "W1"}],
    }
    md2 = builder.plan_to_markdown(impl, plan_kind="implementation")
    assert "## Phases" in md2
    assert "Scaffold" in md2


def test_parse_email_recipients():
    from agent.services import meeting_mail

    addrs = meeting_mail.parse_recipients(
        "email the plan to alice@co.com, bob@co.com and carol@x.org"
    )
    assert addrs == ["alice@co.com", "bob@co.com", "carol@x.org"]


def test_doc_extract_vtt_format():
    from agent.services import doc_extract

    sample = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Alice>Hello world\n"
    fmt = doc_extract.detect_format(sample, filename="Meeting Transcript.vtt", mime="text/vtt")
    assert fmt == "vtt"
    text, status = doc_extract.extract_bytes(sample, filename="Meeting Transcript.vtt")
    assert status == "vtt_extracted"
    assert "Hello world" in text


def test_graph_routes_meeting_intents():
    assert _route_after_plan({"intent": "list_meetings"}) == "meeting_library_agent"
    assert _route_after_plan({"intent": "select_meetings"}) == "meeting_library_agent"
    assert _route_after_plan({"intent": "create_meeting_plan"}) == "meeting_plan_agent"
    assert _route_after_plan({"intent": "create_board_from_plan"}) == "meeting_plan_agent"
    assert _route_after_plan({"intent": "email_meeting_plan"}) == "meeting_email_agent"
    # Existing paths unchanged
    assert _route_after_plan({"intent": "list_docs"}) == "doc_library_agent"
    assert _route_after_plan({"intent": "schedule_meeting"}) == "calendar_agent"


@pytest.mark.asyncio
async def test_planner_meeting_intents(monkeypatch):
    from agent.planner import agent as planner
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", True)

    async def fake_session(phone):
        return {"awaiting": None, "data": {"meeting_selected": [{"pick": 1}]}}

    async def fake_save(*args, **kwargs):
        return None

    monkeypatch.setattr(planner, "get_session", fake_session)
    monkeypatch.setattr("agent.core.session.save_session", fake_save)

    assert (
        await planner.plan({"user_message": "select meetings 1, 3", "whatsapp_phone": "teams:u"})
    )["intent"] == "select_meetings"
    assert (
        await planner.plan(
            {"user_message": "make a plan for the travel agent", "whatsapp_phone": "teams:u"}
        )
    )["intent"] == "create_meeting_plan"
    assert (
        await planner.plan(
            {
                "user_message": "email the plan to alice@co.com",
                "whatsapp_phone": "teams:u",
            }
        )
    )["intent"] == "email_meeting_plan"
    assert (
        await planner.plan(
            {
                "user_message": "create devops board from plan",
                "whatsapp_phone": "teams:u",
            }
        )
    )["intent"] == "create_board_from_plan"


@pytest.mark.asyncio
async def test_planner_board_project_pick_awaiting(monkeypatch):
    from agent.planner import agent as planner
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", True)

    async def fake_session(phone):
        return {
            "awaiting": "meeting_board_project_pick",
            "data": {"meeting_board_projects": [{"name": "A"}, {"name": "B"}]},
        }

    monkeypatch.setattr(planner, "get_session", fake_session)
    out = await planner.plan({"user_message": "2", "whatsapp_phone": "teams:u"})
    assert out["intent"] == "create_board_from_plan"


@pytest.mark.asyncio
async def test_library_select_valid_pick(monkeypatch):
    from agent.agents import meeting_library_agent as lib
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", True)

    async def fake_session(phone):
        return {
            "awaiting": "meeting_pick",
            "data": {
                "meeting_catalog": [
                    {
                        "pick": 1,
                        "title": "A.vtt",
                        "client_name": "Voyager",
                        "agenda_summary": "Discovery",
                    },
                    {
                        "pick": 2,
                        "title": "B.vtt",
                        "client_name": "Other",
                        "agenda_summary": "Ops",
                    },
                ]
            },
        }

    saved = {}

    async def fake_save(phone, **kwargs):
        saved.update(kwargs)
        return None

    monkeypatch.setattr(lib.ms_graph, "graph_configured", lambda: True)
    monkeypatch.setattr(lib, "get_session", fake_session)
    monkeypatch.setattr(lib, "save_session", fake_save)
    out = await lib.run_meeting_library(
        {
            "user_message": "select meetings 1",
            "whatsapp_phone": "teams:u",
            "intent": "select_meetings",
        }
    )
    assert out["status"] == "completed"
    assert len(out["meeting_selected"]) == 1
    assert out["meeting_selected"][0]["client_name"] == "Voyager"
    assert saved.get("data", {}).get("meeting_selected")


@pytest.mark.asyncio
async def test_non_dev_board_handoff_skipped(monkeypatch):
    from agent.agents import meeting_plan_agent as plan_agent
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", True)
    monkeypatch.setattr(settings, "enable_boards_agent", True)

    async def fake_session(phone):
        return {
            "data": {
                "last_plan": {
                    "plan_json": {"title": "Action Plan"},
                    "published": {"docx_url": "https://sp/x.docx"},
                    "meeting_intent": {
                        "meeting_category": "business",
                        "requires_devops_board": False,
                    },
                },
                "meeting_intent": {
                    "meeting_category": "business",
                    "requires_devops_board": False,
                },
            }
        }

    monkeypatch.setattr(plan_agent.ms_graph, "graph_configured", lambda: True)
    monkeypatch.setattr(plan_agent, "get_session", fake_session)
    out = await plan_agent.run_meeting_plan(
        {
            "user_message": "create devops board from plan",
            "whatsapp_phone": "teams:u",
            "intent": "create_board_from_plan",
        }
    )
    assert out["status"] == "completed"
    assert "non-development" in out["notification_text"].lower()


@pytest.mark.asyncio
async def test_publish_uses_themed_folder(monkeypatch):
    from agent.services import meeting_publish

    uploaded = []

    async def fake_upload(**kwargs):
        uploaded.append(kwargs)
        return {"web_url": f"https://sp.example/{kwargs['folder']}/{kwargs['filename']}"}

    monkeypatch.setattr(meeting_publish.graph_docs, "upload_site_drive_item", fake_upload)
    out = await meeting_publish.publish_plan(
        {"title": "Client Discovery"},
        "# Client Discovery\n\n## Action items\n\n| Priority | Owner | Task | Due |\n| --- | --- | --- | --- |\n| High | A | Do | Fri |\n",
        folder="MeetingActionPlans/client-discovery",
        plan_kind="action",
        client_name="Voyager",
    )
    assert out["folder"] == "MeetingActionPlans/client-discovery"
    assert out["plan_kind"] == "action"
    assert any(u["folder"] == "MeetingActionPlans/client-discovery" for u in uploaded)
    assert out["docx_url"] or out["md_url"]


@pytest.mark.asyncio
async def test_library_and_email_disabled_flags(monkeypatch):
    from agent.agents.meeting_library_agent import run_meeting_library
    from agent.agents.meeting_email_agent import run_meeting_email
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", False)
    lib = await run_meeting_library({"user_message": "list my recent meetings"})
    mail = await run_meeting_email({"user_message": "email the plan"})
    assert lib["status"] == "skipped"
    assert mail["status"] == "skipped"


@pytest.mark.asyncio
async def test_channel_gates_meeting_phrase_bypass(monkeypatch):
    """Meeting fabric phrases must bypass coding gates (same path as docs)."""
    from agent.api import channel_gates
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_meeting_intelligence", True)

    scheduled = []

    def schedule(fn, *args, **kwargs):
        scheduled.append((fn, args, kwargs))

    async def fake_get_session(session_id):
        return {
            "awaiting": "plan_approval",
            "data": {"pending_task_id": "abc123"},
        }

    monkeypatch.setattr("agent.core.session.get_session", fake_get_session)

    async def fake_save(*args, **kwargs):
        return None

    monkeypatch.setattr("agent.core.session.save_session", fake_save)

    # Should clear gate and schedule graph — not stay stuck on plan_approval
    await channel_gates.route_inbound_message(
        "teams:user",
        "list my recent meetings",
        schedule=schedule,
        source="teams",
        graph_payload={"phone": "teams:user", "message": "list my recent meetings"},
    )
    assert scheduled, "Meeting phrase should bypass coding gate and schedule work"
