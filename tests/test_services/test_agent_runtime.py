"""Agent run orchestration — busy same-agent, soft queue, status (additive)."""

from __future__ import annotations

import pytest

from agent.services import agent_runtime as runtime


@pytest.fixture
def orch_on(monkeypatch):
    """Opt-in flag is False by default; enable for behavioral tests."""
    monkeypatch.setattr(runtime.settings, "enable_agent_run_orchestration", True)


def test_orchestration_defaults_off():
    assert runtime.orchestration_enabled() is False


def test_resolve_agent_key_prefers_awaiting():
    session = {"awaiting": "azure_finops_action_pick", "data": {}}
    assert runtime.resolve_agent_key(session, "hello") == "azure_finops"
    assert (
        runtime.resolve_agent_key({"awaiting": None, "data": {}}, "check outlook")
        == "check_outlook"
    )


def test_find_busy_same_agent(orch_on):
    session = {
        "data": {
            "agent_active_runs": [
                {"agent": "azure_finops", "task_id": "abc", "started_at": "t"}
            ]
        }
    }
    busy = runtime.find_busy_same_agent(session, agent_key="azure_finops")
    assert busy and busy["task_id"] == "abc"
    assert runtime.find_busy_same_agent(session, agent_key="check_outlook") is None


def test_format_busy_wait_mentions_other_agents():
    text = runtime.format_busy_wait(
        {"agent": "azure_finops", "task_id": "x1", "started_at": "now"},
        other_ok=True,
        queued=True,
    )
    assert "queue" in text.lower()
    assert "Outlook" in text or "different" in text.lower()
    assert "status" in text.lower()


def test_format_orchestration_status_includes_queue_and_buffer(orch_on):
    session = {
        "data": {
            "agent_active_runs": [
                {
                    "agent": "azure_finops",
                    "task_id": "t1",
                    "label": "Azure FinOps Agent",
                    "started_at": "s",
                    "preview": "scan my azure",
                }
            ],
            "agent_run_queue": [
                {
                    "agent": "azure_finops",
                    "label": "Azure FinOps Agent",
                    "preview": "costs",
                }
            ],
            "agent_recent_bubbles": [{"role": "user", "text": "hi"}],
        }
    }
    text = runtime.format_orchestration_status(session)
    assert "Running" in text
    assert "Queued" in text
    assert "bubble" in text.lower()


@pytest.mark.asyncio
async def test_register_and_finish_run(monkeypatch, orch_on):
    store: dict = {"data": {}}

    async def fake_get(_phone):
        return {"data": dict(store["data"])}

    async def fake_save(_phone, data=None, merge_data=True, **_kwargs):
        if data is not None:
            if merge_data:
                store["data"].update(data)
            else:
                store["data"] = dict(data)

    monkeypatch.setattr("agent.core.session.get_session", fake_get)
    monkeypatch.setattr("agent.core.session.save_session", fake_save)

    await runtime.register_run(
        "teams:u1",
        agent_key="azure_finops",
        task_id="tid1",
        user_message="costs",
    )
    assert store["data"]["agent_active_runs"][0]["task_id"] == "tid1"

    await runtime.finish_run("teams:u1", agent_key="azure_finops", task_id="tid1")
    assert store["data"]["agent_active_runs"] == []
    assert store["data"]["agent_run_history"]
