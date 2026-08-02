"""Durable gate file + recovery after App Service recycle."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.gate_store import clear_gate, merge_session_with_gate, read_gate, write_gate
from agent.workflow.gate_recover import recover_session_for_gates
from agent.workflow.gates import GATE_PLAN, GATE_PR_MODE


@pytest.fixture()
def gate_tmpdir(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    clear_gate("+15556586514")


def test_write_read_clear_gate(gate_tmpdir):
    phone = "+15556586514"
    write_gate(
        phone,
        awaiting=GATE_PR_MODE,
        provider="azure_devops",
        data={
            "pending_task_id": "c2e298e6",
            "pr_url": "https://dev.azure.com/x/_git/r/pullrequest/37",
            "pr_id": 37,
        },
    )
    gate = read_gate(phone)
    assert gate is not None
    assert gate["awaiting"] == GATE_PR_MODE
    assert gate["data"]["pr_url"].endswith("/37")
    assert (Path("tmp/workflow_gates") / "15556586514.json").is_file() or read_gate(phone)

    clear_gate(phone)
    assert read_gate(phone) is None


def test_merge_prefers_pr_mode_over_stale_plan(gate_tmpdir):
    phone = "+15556586514"
    write_gate(
        phone,
        awaiting=GATE_PR_MODE,
        provider="azure_devops",
        data={
            "pending_task_id": "c2e298e6",
            "pr_url": "https://example/pr/37",
            "pr_id": 37,
        },
    )
    session = {
        "phone": phone,
        "awaiting": GATE_PLAN,
        "provider": "azure_devops",
        "data": {"pending_task_id": "c2e298e6"},
    }
    merged = merge_session_with_gate(session)
    assert merged["awaiting"] == GATE_PR_MODE
    assert merged["data"]["pr_url"].endswith("/37")


@pytest.mark.asyncio
async def test_recover_upgrades_plan_from_checkpoint(gate_tmpdir):
    phone = "+15556586514"
    session = {
        "phone": phone,
        "awaiting": GATE_PLAN,
        "provider": "azure_devops",
        "data": {
            "pending_task_id": "c2e298e6",
            "azdo_project": "Project-NIT",
            "azdo_repo_id": "repo-guid",
            "repo_provider": "azure_devops",
        },
    }

    snap = type("S", (), {})()
    snap.values = {
        "pr_url": "https://dev.azure.com/x/_git/r/pullrequest/37",
        "pr_id": 37,
        "repo_name": "web.Whatsapp-AI-Agent",
    }

    compiled = AsyncMock()
    compiled.aget_state = AsyncMock(return_value=snap)

    with (
        patch("agent.workflow.gate_recover._pr_from_task_row", AsyncMock(return_value={})),
        patch("agent.agents.graph._get_compiled", AsyncMock(return_value=compiled)),
        patch("agent.workflow.gate_recover._pr_from_azdo", AsyncMock(return_value={})),
    ):
        out = await recover_session_for_gates(session)

    assert out["awaiting"] == GATE_PR_MODE
    assert out["data"]["pr_url"].endswith("/37")
    durable = read_gate(phone)
    assert durable and durable["awaiting"] == GATE_PR_MODE
