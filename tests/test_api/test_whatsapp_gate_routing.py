"""Simulate WhatsApp webhook gate routing without hitting Meta/AzDO."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.main import app
from agent.workflow.gates import GATE_PLAN, GATE_PR_MODE


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


@pytest.mark.asyncio
async def test_webhook_one_after_stale_plan_triggers_pr_ai_resume():
    """User reply '1' with plan_approval+pr_url must resume PR AI review, not re-show plan."""
    phone = "919643877357"
    session = {
        "phone": phone,
        "awaiting": GATE_PLAN,
        "provider": "azure_devops",
        "data": {
            "pending_task_id": "28e0e1f7",
            "pr_url": "https://dev.azure.com/x/_git/r/pullrequest/36",
            "pr_id": 36,
            "repo_name": "web.Whatsapp-AI-Agent",
            "repo_provider": "azure_devops",
        },
    }

    heal = AsyncMock()
    with (
        patch("agent.core.security.is_phone_allowed", return_value=True),
        patch("agent.core.security.verify_whatsapp_signature", return_value=True),
        patch("agent.core.session.get_session", new_callable=AsyncMock, return_value=session),
        patch(
            "agent.workflow.gate_recover.recover_session_for_gates",
            new_callable=AsyncMock,
            return_value={
                **session,
                "awaiting": GATE_PR_MODE,
            },
        ),
        patch("agent.api.whatsapp._heal_pr_mode_and_resume", heal),
        patch("agent.api.whatsapp._resume_gate", new_callable=AsyncMock) as resume,
        patch("agent.api.whatsapp._send_gate_hint", new_callable=AsyncMock) as hint,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # FastAPI BackgroundTasks run after response in TestClient/httpx ASGI —
            # invoke endpoint and drain tasks via Starlette behavior.
            resp = await client.post(
                "/webhooks/whatsapp",
                json=_whatsapp_text_payload(phone, "1"),
                headers={"X-Hub-Signature-256": "sha256=test"},
            )
        assert resp.status_code == 200

    # Background task may run after response; give event loop a tick
    import asyncio

    await asyncio.sleep(0.05)

    assert heal.await_count >= 1 or resume.await_count >= 1
    assert hint.await_count == 0
    if heal.await_count:
        args = heal.await_args.args
        assert args[0] == phone
        assert args[3] == "ai"
    if resume.await_count:
        assert resume.await_args.kwargs.get("pr_review_mode") == "ai" or (
            resume.await_args.args[0] == GATE_PR_MODE
        )


@pytest.mark.asyncio
async def test_webhook_one_recovers_pr_without_session_pr_url():
    """Recycle case: SQL still plan_approval, no pr_url — recover then AI review."""
    phone = "919643877357"
    session = {
        "phone": phone,
        "awaiting": GATE_PLAN,
        "provider": "azure_devops",
        "data": {
            "pending_task_id": "c2e298e6",
            "repo_provider": "azure_devops",
            "azdo_project": "Project-NIT",
            "azdo_repo_id": "abc",
        },
    }
    recovered = {
        **session,
        "awaiting": GATE_PR_MODE,
        "data": {
            **session["data"],
            "pr_url": "https://dev.azure.com/x/_git/r/pullrequest/37",
            "pr_id": 37,
        },
    }

    heal = AsyncMock()
    with (
        patch("agent.core.security.is_phone_allowed", return_value=True),
        patch("agent.core.security.verify_whatsapp_signature", return_value=True),
        patch("agent.core.session.get_session", new_callable=AsyncMock, return_value=session),
        patch(
            "agent.workflow.gate_recover.recover_session_for_gates",
            new_callable=AsyncMock,
            return_value=recovered,
        ),
        patch("agent.api.whatsapp._heal_pr_mode_and_resume", heal),
        patch("agent.api.whatsapp._resume_gate", new_callable=AsyncMock) as resume,
        patch("agent.api.whatsapp._send_gate_hint", new_callable=AsyncMock) as hint,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/webhooks/whatsapp",
                json=_whatsapp_text_payload(phone, "1"),
                headers={"X-Hub-Signature-256": "sha256=test"},
            )
        assert resp.status_code == 200

    import asyncio

    await asyncio.sleep(0.05)

    assert heal.await_count + resume.await_count >= 1
    assert hint.await_count == 0


@pytest.mark.asyncio
async def test_webhook_one_on_plan_without_pr_does_not_reshow_plan():
    """Bare '1' on plan_approval must never re-prompt Gate 1 plan approval."""
    phone = "919643877357"
    session = {
        "phone": phone,
        "awaiting": GATE_PLAN,
        "provider": "azure_devops",
        "data": {"pending_task_id": "c2e298e6"},
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
        patch("agent.api.whatsapp._heal_pr_mode_and_resume", new_callable=AsyncMock) as heal,
        patch("agent.api.whatsapp._resume_gate", new_callable=AsyncMock) as resume,
        patch("agent.api.whatsapp._send_gate_hint", new_callable=AsyncMock) as hint,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/webhooks/whatsapp",
                json=_whatsapp_text_payload(phone, "1"),
                headers={"X-Hub-Signature-256": "sha256=test"},
            )
        assert resp.status_code == 200

    import asyncio

    await asyncio.sleep(0.05)
    assert heal.await_count == 0
    assert resume.await_count == 0
    assert hint.await_count >= 1
    text = hint.await_args.args[1]
    assert "plan approval" not in text.lower()
    assert "Gate 1" not in text
    assert "pull request" in text.lower() or "PR" in text


@pytest.mark.asyncio
async def test_webhook_one_during_provider_wizard_not_forced_to_pr():
    """Bare '1' while choosing GitHub/AzDO must still go to repo wizard, not PR review."""
    phone = "919643877357"
    session = {
        "phone": phone,
        "awaiting": "provider",
        "provider": None,
        "data": {},
    }
    handle = AsyncMock()
    with (
        patch("agent.core.security.is_phone_allowed", return_value=True),
        patch("agent.core.security.verify_whatsapp_signature", return_value=True),
        patch("agent.core.session.get_session", new_callable=AsyncMock, return_value=session),
        patch(
            "agent.workflow.gate_recover.recover_session_for_gates",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch("agent.api.whatsapp._heal_pr_mode_and_resume", new_callable=AsyncMock) as heal,
        patch("agent.api.whatsapp._resume_gate", new_callable=AsyncMock) as resume,
        patch(
            "agent.core.background.handle_whatsapp_message",
            handle,
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/webhooks/whatsapp",
                json=_whatsapp_text_payload(phone, "1"),
                headers={"X-Hub-Signature-256": "sha256=test"},
            )
        assert resp.status_code == 200

    import asyncio

    await asyncio.sleep(0.05)
    assert heal.await_count == 0
    assert resume.await_count == 0
    assert handle.await_count >= 1
