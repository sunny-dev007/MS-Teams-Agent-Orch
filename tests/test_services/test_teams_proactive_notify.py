"""Orbit proactive Teams delivery — no Status required for completions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.services import channel_notify, teams_proactive


@pytest.mark.asyncio
async def test_teams_notify_uses_proactive_when_ref_present():
    with (
        patch.object(
            teams_proactive,
            "load_conversation_reference",
            AsyncMock(return_value={"service_url": "https://smba.trafficmanager.net/in/", "conversation": {"id": "c1"}}),
        ),
        patch.object(
            teams_proactive,
            "try_send_proactive_teams_message",
            AsyncMock(return_value=True),
        ) as proactive,
        patch("agent.core.channel_outbox.enqueue_outbox", AsyncMock()) as enqueue,
    ):
        await channel_notify.send_channel_message("teams:user-1", "Choose provider\n1. GitHub")
        proactive.assert_awaited_once()
        enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_teams_notify_falls_back_to_outbox_without_ref():
    with (
        patch.object(
            teams_proactive,
            "load_conversation_reference",
            AsyncMock(return_value=None),
        ),
        patch.object(
            teams_proactive,
            "try_send_proactive_teams_message",
            AsyncMock(return_value=False),
        ),
        patch("agent.core.channel_outbox.enqueue_outbox", AsyncMock()) as enqueue,
    ):
        await channel_notify.send_channel_message("teams:user-2", "Choose provider")
        enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_orbit_skips_duplicate_working_ack_when_ref_present():
    with (
        patch.object(
            teams_proactive,
            "load_conversation_reference",
            AsyncMock(return_value={"service_url": "https://x", "conversation": {"id": "c1"}}),
        ),
        patch.object(
            teams_proactive,
            "try_send_proactive_teams_message",
            AsyncMock(return_value=True),
        ) as proactive,
        patch("agent.core.channel_outbox.enqueue_outbox", AsyncMock()) as enqueue,
    ):
        await channel_notify.send_channel_message(
            "teams:user-3", "Got it, Sunny — working on it…"
        )
        proactive.assert_not_awaited()
        enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_whatsapp_notify_unchanged():
    with patch("agent.services.whatsapp.send_message", AsyncMock()) as wa:
        await channel_notify.send_channel_message("919999999999", "hello from graph")
        wa.assert_awaited_once_with("919999999999", "hello from graph")


def test_conversation_reference_roundtrip_keeps_service_url():
    from botbuilder.core import TurnContext
    from botbuilder.schema import Activity, ChannelAccount, ConversationAccount

    act = Activity(
        type="message",
        id="1",
        service_url="https://smba.trafficmanager.net/in/",
        channel_id="msteams",
        from_property=ChannelAccount(id="u1"),
        recipient=ChannelAccount(id="b1"),
        conversation=ConversationAccount(id="c1"),
    )
    ref = TurnContext.get_conversation_reference(act)
    data = teams_proactive._reference_to_dict(ref)
    restored = teams_proactive._reference_from_dict(data)
    assert restored.service_url == "https://smba.trafficmanager.net/in/"


def test_microsoft_app_credentials_import_for_proactive():
    """Regression: wrong botbuilder.core import broke all Orbit background pushes."""
    from agent.services.teams_proactive import (
        proactive_credentials_probe,
        resolve_microsoft_app_credentials,
    )

    cls = resolve_microsoft_app_credentials()
    assert hasattr(cls, "trust_service_url")
    probe = proactive_credentials_probe()
    assert probe["ok"] is True
    assert probe["has_trust_service_url"] is True
