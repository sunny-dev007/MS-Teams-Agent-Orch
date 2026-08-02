"""CI test-fixer gate routing and failure offer copy."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.main import app
from agent.services.ci_watch import CiTerminalResult, format_pr_validation_notification
from agent.workflow.gates import GATE_CI_FIX, GATE_PIPELINE_WATCH


def _whatsapp_text_payload(phone: str, text: str) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": phone,
                                    "id": "wamid.test",
                                    "timestamp": "1",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                            "contacts": [{"wa_id": phone, "profile": {"name": "Sunny"}}],
                        }
                    }
                ]
            }
        ]
    }


def test_pr_validation_success_copy_not_deployment():
    text = format_pr_validation_notification(
        {
            "task_id": "abc12345",
            "pr_url": "https://dev.azure.com/x/_git/r/pullrequest/1",
            "pipeline_url": "https://dev.azure.com/x/p/_build?definitionId=15",
        },
        CiTerminalResult(outcome="succeeded", url="https://dev.azure.com/x/p/_build/results?buildId=9"),
    )
    assert "PR checks passed" in text
    assert "Deployment completed" not in text


@pytest.mark.asyncio
async def test_webhook_fix_tests_runs_fixer():
    phone = "919643877357"
    session = {
        "phone": phone,
        "awaiting": GATE_CI_FIX,
        "provider": "azure_devops",
        "data": {
            "pending_task_id": "c2e298e6",
            "ci_build_id": "123",
            "ci_failure_summary": "FAILED tests/test_api/test_whatsapp_webhook.py",
            "repo_url": "https://dev.azure.com/Az-FullStack/Project-NIT/_git/web.Whatsapp-AI-Agent",
            "azdo_project": "Project-NIT",
        },
    }
    fixer = AsyncMock()
    with (
        patch("agent.core.security.is_phone_allowed", return_value=True),
        patch("agent.core.security.verify_whatsapp_signature", return_value=True),
        patch("agent.core.session.get_session", new_callable=AsyncMock, return_value=session),
        patch(
            "agent.workflow.gate_recover.recover_session_for_gates",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch("agent.api.whatsapp._run_ci_test_fixer", fixer),
        patch("agent.api.whatsapp._send_gate_hint", new_callable=AsyncMock) as hint,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/webhooks/whatsapp",
                json=_whatsapp_text_payload(phone, "FIX TESTS"),
                headers={"X-Hub-Signature-256": "sha256=test"},
            )
        assert resp.status_code == 200

    import asyncio

    await asyncio.sleep(0.05)
    assert fixer.await_count >= 1
    assert hint.await_count == 0


@pytest.mark.asyncio
async def test_webhook_pipeline_watching_keeps_context_hint():
    phone = "919643877357"
    session = {
        "phone": phone,
        "awaiting": GATE_PIPELINE_WATCH,
        "provider": "azure_devops",
        "data": {"pending_task_id": "c2e298e6", "pipeline_url": "https://example/build"},
    }
    with (
        patch("agent.core.security.is_phone_allowed", return_value=True),
        patch("agent.core.security.verify_whatsapp_signature", return_value=True),
        patch("agent.core.session.get_session", new_callable=AsyncMock, return_value=session),
        patch(
            "agent.workflow.gate_recover.recover_session_for_gates",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch("agent.api.whatsapp._send_gate_hint", new_callable=AsyncMock) as hint,
        patch("agent.api.whatsapp._run_ci_test_fixer", new_callable=AsyncMock) as fixer,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/webhooks/whatsapp",
                json=_whatsapp_text_payload(phone, "hello"),
                headers={"X-Hub-Signature-256": "sha256=test"},
            )
        assert resp.status_code == 200

    import asyncio

    await asyncio.sleep(0.05)
    assert fixer.await_count == 0
    assert hint.await_count >= 1
    assert "STOP" in hint.await_args.args[1] or "Pipelines" in hint.await_args.args[1]
