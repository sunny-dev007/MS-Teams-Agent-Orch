"""Outlook + Azure Boards agents — flags, routing, WIQL, project/ticket flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.config import Settings
from agent.services import azure_boards


def test_decode_token_roles_and_clear_cache():
    import base64
    import json

    from agent.services import ms_graph

    payload = {"roles": ["Sites.ReadWrite.All", "Mail.Read", "User.Read.All"]}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"hdr.{raw}.sig"
    assert "Mail.Read" in ms_graph.decode_token_roles(token)

    ms_graph._token_cache["access_token"] = "stale"
    ms_graph._token_cache["expires_at"] = 9_999_999_999.0
    ms_graph.clear_app_token_cache()
    assert ms_graph._token_cache["access_token"] == ""
    assert ms_graph._token_cache["expires_at"] == 0.0


@pytest.mark.asyncio
async def test_graph_request_retries_once_on_403(monkeypatch):
    import json

    import httpx

    from agent.services import ms_graph

    calls = {"n": 0}

    class FakeResp:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body
            self.text = json.dumps(body)
            self.content = self.text.encode()
            self.request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/x")

        def json(self):
            return self._body

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, json=None, params=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResp(
                    403, {"error": {"code": "ErrorAccessDenied", "message": "denied"}}
                )
            return FakeResp(200, {"value": []})

    monkeypatch.setattr(ms_graph, "graph_configured", lambda: True)
    monkeypatch.setattr(ms_graph, "get_app_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(ms_graph.httpx, "AsyncClient", FakeClient)

    body = await ms_graph.graph_request("GET", "/users/x/messages")
    assert body == {"value": []}
    assert calls["n"] == 2


def test_boards_wiql_never_uses_at_me():
    wiql = azure_boards.build_assigned_wiql("sunny@contoso.com", project="Project-NIT")
    assert "@Me" not in wiql
    assert "sunny@contoso.com" in wiql
    assert "System.AssignedTo" in wiql
    assert "Priority" in wiql


def test_boards_wiql_escapes_quote():
    wiql = azure_boards.build_assigned_wiql("o'brien@contoso.com")
    assert "o''brien@contoso.com" in wiql


def test_sort_and_metrics_by_priority():
    items = [
        {"id": 1, "type": "Bug", "priority": 3, "changed": "2026-01-01"},
        {"id": 2, "type": "Bug", "priority": 1, "changed": "2026-01-02"},
        {"id": 3, "type": "User Story", "priority": 2, "changed": "2026-01-03"},
    ]
    sorted_items = azure_boards.sort_items_by_priority(items)
    assert [x["id"] for x in sorted_items] == [2, 3, 1]
    m = azure_boards.summarize_metrics(sorted_items)
    assert m["open"] == 3
    assert m["bugs"] == 2
    assert m["stories"] == 1
    assert m["high_priority"] == 2


def test_coding_instruction_includes_work_item():
    text = azure_boards.build_coding_instruction(
        {
            "id": 42,
            "title": "Fix login",
            "type": "Bug",
            "state": "Active",
            "priority": 1,
            "url": "https://dev.azure.com/x/_workitems/edit/42",
            "description": "Users cannot login on mobile.",
        }
    )
    assert "#42" in text
    assert "Fix login" in text
    assert "mobile" in text


@pytest.mark.asyncio
async def test_outlook_disabled_message(monkeypatch):
    from agent.agents.outlook_agent import read_outlook
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_outlook_agent", False)
    out = await read_outlook(
        {"user_message": "check my outlook", "whatsapp_phone": "teams:oid-1"}
    )
    assert out["status"] == "skipped"


@pytest.mark.asyncio
async def test_boards_asks_project_when_multiple(monkeypatch):
    from agent.agents.boards_agent import list_my_boards
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_boards_agent", True)
    monkeypatch.setattr(settings, "azdo_boards_project", "")
    monkeypatch.setattr(
        "agent.services.azure_boards.boards_configured", lambda: True
    )
    monkeypatch.setattr(
        "agent.services.azure_boards.resolve_identity_email",
        AsyncMock(
            return_value={
                "email": "s@x.com",
                "display_name": "Sunny",
                "upn": "s@x.com",
                "mail": "s@x.com",
                "id": "oid",
            }
        ),
    )
    monkeypatch.setattr(
        "agent.services.azure_boards.list_org_projects",
        AsyncMock(
            return_value=[
                {"id": "1", "name": "Project-A"},
                {"id": "2", "name": "Project-B"},
            ]
        ),
    )
    monkeypatch.setattr(
        "agent.agents.boards_agent.save_session",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "agent.services.workspace_handoff.mark_workspace",
        AsyncMock(),
    )
    out = await list_my_boards(
        {"user_message": "my action items", "whatsapp_phone": "teams:oid-1"}
    )
    assert out["session_awaiting"] == "boards_project_pick"
    assert "Project-A" in out["notification_text"]
    assert "choose a project" in out["notification_text"].lower()


@pytest.mark.asyncio
async def test_boards_fetch_after_project_pick(monkeypatch):
    from agent.agents.boards_agent import list_my_boards
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_boards_agent", True)
    monkeypatch.setattr(
        "agent.services.azure_boards.boards_configured", lambda: True
    )
    monkeypatch.setattr(
        "agent.services.azure_boards.resolve_identity_email",
        AsyncMock(
            return_value={
                "email": "s@x.com",
                "display_name": "Sunny",
                "upn": "s@x.com",
                "mail": "s@x.com",
                "id": "oid",
            }
        ),
    )
    monkeypatch.setattr(
        "agent.services.azure_boards.list_assigned_work_items",
        AsyncMock(
            return_value=[
                {
                    "id": 42,
                    "title": "Fix login",
                    "state": "Active",
                    "type": "Bug",
                    "priority": 1,
                    "url": "https://dev.azure.com/x/_workitems/edit/42",
                    "project": "Project-A",
                }
            ]
        ),
    )
    monkeypatch.setattr("agent.agents.boards_agent.save_session", AsyncMock())
    monkeypatch.setattr(
        "agent.services.workspace_handoff.mark_workspace", AsyncMock()
    )
    out = await list_my_boards(
        {
            "user_message": "1",
            "whatsapp_phone": "teams:oid-1",
            "session_awaiting": "boards_project_pick",
            "session_data": {
                "boards_projects": [
                    {"id": "1", "name": "Project-A"},
                    {"id": "2", "name": "Project-B"},
                ]
            },
        }
    )
    assert out["session_awaiting"] == "boards_ticket_pick"
    assert "Fix login" in out["notification_text"]
    assert "Metric" in out["notification_text"]
    assert "Start development" in out["notification_text"]


@pytest.mark.asyncio
async def test_boards_ticket_seeds_repo_wizard(monkeypatch):
    from agent.agents.boards_agent import list_my_boards
    from agent.config import settings

    monkeypatch.setattr(settings, "enable_boards_agent", True)
    monkeypatch.setattr(
        "agent.services.azure_boards.boards_configured", lambda: True
    )
    monkeypatch.setattr(
        "agent.services.azure_boards.resolve_identity_email",
        AsyncMock(
            return_value={
                "email": "s@x.com",
                "display_name": "Sunny",
                "upn": "s@x.com",
                "mail": "s@x.com",
                "id": "oid",
            }
        ),
    )
    monkeypatch.setattr(
        "agent.services.azure_boards.get_work_item",
        AsyncMock(
            return_value={
                "id": 42,
                "title": "Fix login",
                "type": "Bug",
                "state": "Active",
                "priority": 1,
                "project": "Project-A",
                "description": "Broken OAuth",
                "url": "https://dev.azure.com/x/_workitems/edit/42",
            }
        ),
    )
    monkeypatch.setattr(
        "agent.specialists.azdo_agent.azdo_agent.list_repositories",
        AsyncMock(
            return_value=[
                {"id": "r1", "name": "web.app", "remoteUrl": "https://dev.azure.com/x/_git/web.app"}
            ]
        ),
    )
    monkeypatch.setattr("agent.agents.boards_agent.save_session", AsyncMock())
    out = await list_my_boards(
        {
            "user_message": "#42",
            "whatsapp_phone": "teams:oid-1",
            "intent": "boards_start_dev",
            "session_awaiting": "boards_ticket_pick",
            "session_data": {
                "boards_project": "Project-A",
                "boards_catalog": [{"id": 42, "title": "Fix login"}],
            },
        }
    )
    assert out["intent"] == "browse_repos"
    assert out["session_awaiting"] == "repo"
    assert "web.app" in out["notification_text"]
    assert "42" in out["notification_text"]


@pytest.mark.asyncio
async def test_planner_boards_and_action_items():
    from agent.planner.agent import plan

    r1 = await plan(
        {
            "user_message": "my action items",
            "task_id": "t1",
            "whatsapp_phone": "teams:oid-1",
        }
    )
    assert r1["intent"] == "list_boards"


@pytest.mark.asyncio
async def test_planner_ticket_while_awaiting(monkeypatch):
    from agent.planner.agent import plan

    async def fake_session(phone):
        return {
            "awaiting": "boards_ticket_pick",
            "data": {"boards_catalog": [{"id": 42}]},
        }

    monkeypatch.setattr("agent.planner.agent.get_session", fake_session)
    result = await plan(
        {
            "user_message": "#42",
            "task_id": "t1",
            "whatsapp_phone": "teams:oid-1",
        }
    )
    assert result["intent"] == "boards_start_dev"


@pytest.mark.asyncio
async def test_planner_whatsapp_emails_stay_gmail(monkeypatch):
    from agent.config import settings
    from agent.planner.agent import plan

    monkeypatch.setattr(settings, "enable_outlook_agent", True)
    result = await plan(
        {
            "user_message": "check my emails",
            "task_id": "t1",
            "whatsapp_phone": "+15551212",
        }
    )
    assert result["intent"] == "check_email"


@pytest.mark.asyncio
async def test_graph_routes_boards_start_dev():
    from agent.agents.graph import _route_after_plan, build_graph

    assert _route_after_plan({"intent": "boards_start_dev"}) == "boards_agent"
    g = build_graph()
    assert "boards_agent" in g.nodes


@pytest.mark.asyncio
async def test_repo_wizard_auto_starts_with_pending_instruction(monkeypatch):
    from agent.specialists.repo_wizard import repo_wizard

    session = {
        "awaiting": "repo",
        "provider": "azure_devops",
        "data": {
            "azdo_project": "Project-A",
            "repos": [
                {"id": "r1", "name": "web.app", "remoteUrl": "https://dev.azure.com/x/_git/web.app"}
            ],
            "pending_code_instruction": "Implement Azure Boards Bug #42: Fix login",
            "boards_work_item_id": 42,
        },
    }

    monkeypatch.setattr(
        "agent.specialists.repo_wizard.get_session",
        AsyncMock(return_value=session),
    )
    monkeypatch.setattr(
        "agent.specialists.repo_wizard.save_session",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "agent.specialists.repo_wizard.clear_session",
        AsyncMock(),
    )
    out = await repo_wizard.run(
        {
            "user_message": "1",
            "whatsapp_phone": "teams:oid-1",
            "session_awaiting": "repo",
            "repo_provider": "azure_devops",
        }
    )
    assert out["intent"] == "code_change"
    assert "Fix login" in out["user_message"]
    assert out["repo_url"]
