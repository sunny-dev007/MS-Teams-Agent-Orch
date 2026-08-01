import pytest

from agent.services.ci_gate import (
    CiBusyInfo,
    check_deploy_blocked,
    context_from_session_data,
    deploy_lock_key,
    release_deploy,
    try_acquire_deploy,
)


def test_deploy_lock_keys_are_provider_isolated():
    gh = deploy_lock_key("github", owner="sunny", repo_name="personal-task-api")
    az = deploy_lock_key(
        "azure_devops", project="Project-NIT", repo_name="web.Whatsapp-AI-Agent"
    )
    assert gh.startswith("github:")
    assert az.startswith("azure_devops:")
    assert gh != az


def test_context_from_session_data():
    ctx = context_from_session_data(
        {
            "repo_provider": "azure_devops",
            "repo_name": "web.Whatsapp-AI-Agent",
            "azdo_project": "Project-NIT",
            "azdo_repo_id": "abc",
        }
    )
    assert ctx["provider"] == "azure_devops"
    assert ctx["project"] == "Project-NIT"
    assert ctx["repo_id"] == "abc"


@pytest.mark.asyncio
async def test_deploy_lock_blocks_second_task():
    key = deploy_lock_key("github", owner="o", repo_name="r")
    assert await try_acquire_deploy(key, "task-a") is True
    assert await try_acquire_deploy(key, "task-b") is False
    await release_deploy(key, "task-a")
    assert await try_acquire_deploy(key, "task-b") is True
    await release_deploy(key, "task-b")


@pytest.mark.asyncio
async def test_check_deploy_blocked_uses_active_lock(monkeypatch):
    key = deploy_lock_key("github", owner="o", repo_name="r")
    assert await try_acquire_deploy(key, "task-a") is True

    async def _no_ci(_ctx):
        return CiBusyInfo(busy=False)

    monkeypatch.setattr(
        "agent.services.ci_gate.check_ci_busy_for_context",
        _no_ci,
    )
    blocked = await check_deploy_blocked(
        {"provider": "github", "owner": "o", "repo_name": "r"},
        task_id="task-b",
    )
    assert blocked.busy is True
    assert "task-a" in blocked.detail
    await release_deploy(key, "task-a")


def test_wait_message_mentions_pipeline():
    msg = CiBusyInfo(
        busy=True,
        provider="GitHub Actions",
        label="sunny/personal-task-api",
        url="https://github.com/sunny/personal-task-api/actions",
        detail="Active run: ci (in_progress).",
    ).wait_message()
    assert "Please wait" in msg
    assert "personal-task-api" in msg
    assert "GitHub Actions" in msg
