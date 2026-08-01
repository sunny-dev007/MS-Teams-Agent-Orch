import pytest

from agent.services import azure_devops as azdo


@pytest.mark.asyncio
async def test_find_pipeline_for_repo_exact_match(monkeypatch):
    async def _pipes(_project):
        return [
            {"id": 12, "name": "Linux.SmartDocs-WebApp"},
            {"id": 15, "name": "web.Whatsapp-AI-Agent"},
            {"id": 5, "name": "Micro_Service_Users"},
        ]

    monkeypatch.setattr(azdo, "list_pipelines", _pipes)
    match = await azdo.find_pipeline_for_repo("Project-NIT", "web.Whatsapp-AI-Agent")
    assert match is not None
    assert match["id"] == 15


@pytest.mark.asyncio
async def test_find_pipeline_never_returns_smartdocs_for_agent_repo(monkeypatch):
    async def _pipes(_project):
        return [
            {"id": 12, "name": "Linux.SmartDocs-WebApp"},
            {"id": 15, "name": "web.Whatsapp-AI-Agent"},
        ]

    monkeypatch.setattr(azdo, "list_pipelines", _pipes)
    match = await azdo.find_pipeline_for_repo("Project-NIT", "web.Whatsapp-AI-Agent")
    assert match["id"] != 12
    assert match["name"] == "web.Whatsapp-AI-Agent"


@pytest.mark.asyncio
async def test_trigger_pipeline_refuses_cross_repo(monkeypatch):
    async def _pipes(_project):
        return [
            {"id": 12, "name": "Linux.SmartDocs-WebApp"},
            {"id": 15, "name": "web.Whatsapp-AI-Agent"},
        ]

    monkeypatch.setattr(azdo, "list_pipelines", _pipes)

    with pytest.raises(RuntimeError, match="Refusing to trigger"):
        await azdo.trigger_pipeline(
            "Project-NIT",
            12,  # SmartDocs — must not run for WhatsApp agent repo
            "main",
            expected_repo_name="web.Whatsapp-AI-Agent",
        )


@pytest.mark.asyncio
async def test_list_active_builds_empty_without_repo_scope(monkeypatch):
    called = {"list": False}

    async def _builds(*_a, **_k):
        called["list"] = True
        return [{"id": 1, "status": "inProgress"}]

    monkeypatch.setattr(azdo, "list_builds", _builds)
    out = await azdo.list_active_builds("Project-NIT")
    assert out == []
    assert called["list"] is False
