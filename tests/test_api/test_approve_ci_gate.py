import pytest

from agent.services.ci_gate import CiBusyInfo


@pytest.mark.asyncio
async def test_approve_blocked_when_ci_busy(monkeypatch):
    from agent.api import whatsapp as wa

    async def _resolve(_phone, explicit):
        return explicit or "abc12345"

    async def _ctx(_phone, _task_id):
        return {
            "provider": "github",
            "owner": "sunny-dev007",
            "repo_name": "personal-task-api",
            "project": "",
            "repo_id": "",
            "repo_url": "",
        }

    async def _blocked(_ctx, task_id=None):
        return CiBusyInfo(
            busy=True,
            provider="GitHub Actions",
            label="sunny-dev007/personal-task-api",
            url="https://github.com/sunny-dev007/personal-task-api/actions",
            detail="Active run: ci (in_progress).",
        )

    sent: list[str] = []

    async def _send(_phone, text):
        sent.append(text)

    resumed = {"called": False}

    async def _resume(*_a, **_k):
        resumed["called"] = True

    monkeypatch.setattr(wa, "_resolve_task_id", _resolve)
    monkeypatch.setattr(wa, "_pending_deploy_context", _ctx)
    monkeypatch.setattr("agent.services.ci_gate.check_deploy_blocked", _blocked)
    monkeypatch.setattr("agent.services.whatsapp.send_message", _send)
    monkeypatch.setattr("agent.agents.graph.resume_graph", _resume)

    await wa._resume_with_approval("abc12345", "approved", "919643877357")

    assert resumed["called"] is False
    assert sent
    assert "Please wait" in sent[0]
    assert "pipeline" in sent[0].lower() or "GitHub Actions" in sent[0]


@pytest.mark.asyncio
async def test_approve_proceeds_when_ci_idle(monkeypatch):
    from agent.api import whatsapp as wa

    async def _resolve(_phone, explicit):
        return "abc12345"

    async def _ctx(_phone, _task_id):
        return {
            "provider": "azure_devops",
            "owner": "",
            "repo_name": "web.Whatsapp-AI-Agent",
            "project": "Project-NIT",
            "repo_id": "rid",
            "repo_url": "",
        }

    async def _blocked(_ctx, task_id=None):
        return CiBusyInfo(busy=False)

    async def _acquire(_key, _task_id):
        return True

    async def _release(_key, _task_id):
        return None

    sent: list[str] = []

    async def _send(_phone, text):
        sent.append(text)

    resumed = {"called": False}

    async def _resume(*_a, **_k):
        resumed["called"] = True

    monkeypatch.setattr(wa, "_resolve_task_id", _resolve)
    monkeypatch.setattr(wa, "_pending_deploy_context", _ctx)
    monkeypatch.setattr("agent.services.ci_gate.check_deploy_blocked", _blocked)
    monkeypatch.setattr("agent.services.ci_gate.try_acquire_deploy", _acquire)
    monkeypatch.setattr("agent.services.ci_gate.release_deploy", _release)
    monkeypatch.setattr("agent.services.whatsapp.send_message", _send)
    monkeypatch.setattr("agent.agents.graph.resume_graph", _resume)

    await wa._resume_with_approval(None, "approved", "919643877357")

    assert resumed["called"] is True
    assert any("CI is clear" in m for m in sent)
