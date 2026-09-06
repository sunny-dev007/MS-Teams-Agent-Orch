"""Orbit must deliver picker/content in-turn — never drop joined ack+content."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.api import teams_bot
from agent.services import channel_notify
from agent.services.orbit_turn import is_orbit_turn_active, orbit_turn

ACK = teams_bot.WORKING_ACK
PICKER = (
    "Choose repository provider\n\n"
    "1. GitHub  (github)\n"
    "2. Azure DevOps  (azdo)\n"
    "Reply with 1 / 2 or the name."
)


def test_orbit_bot_singleton_exists():
    assert hasattr(teams_bot, "_BOT")
    assert teams_bot._BOT is not None
    assert hasattr(teams_bot._BOT, "on_turn")
    assert callable(teams_bot._map_teams_attachments)


def test_strip_leading_ack_keeps_picker():
    joined = f"{ACK}\n\n---\n\n{PICKER}"
    assert joined.lower().startswith("got it, sunny — working on it")
    stripped = teams_bot._strip_leading_working_ack(joined)
    assert "Choose repository provider" in stripped
    assert "Got it, Sunny" not in stripped


def test_strip_ack_only_returns_empty():
    assert teams_bot._strip_leading_working_ack(ACK) == ""


def test_working_ack_blob_exact_only():
    assert teams_bot._is_working_ack_blob(ACK) is True
    assert teams_bot._is_working_ack_blob(f"{ACK}\n\n---\n\n{PICKER}") is False


@pytest.mark.asyncio
async def test_orbit_turn_forces_outbox_not_proactive():
    with (
        patch(
            "agent.services.teams_proactive.try_send_proactive_teams_message",
            AsyncMock(return_value=True),
        ) as proactive,
        patch("agent.core.channel_outbox.enqueue_outbox", AsyncMock()) as enqueue,
    ):
        with orbit_turn():
            assert is_orbit_turn_active() is True
            await channel_notify.send_channel_message("teams:user-1", PICKER)
        proactive.assert_not_awaited()
        enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_whatsapp_still_uses_graph():
    with patch("agent.services.whatsapp.send_message", AsyncMock()) as wa:
        await channel_notify.send_channel_message("919999999999", "hello")
        wa.assert_awaited_once_with("919999999999", "hello")


@pytest.mark.asyncio
async def test_orbit_turn_not_sticky_after_exit():
    with orbit_turn():
        assert is_orbit_turn_active() is True
    assert is_orbit_turn_active() is False
    # After turn, notify must try proactive (not force outbox-only).
    with (
        patch(
            "agent.services.teams_proactive.load_conversation_reference",
            AsyncMock(return_value={"service_url": "https://smba.example/"}),
        ),
        patch(
            "agent.services.teams_proactive.try_send_proactive_teams_message",
            AsyncMock(return_value=True),
        ) as proactive,
        patch("agent.core.channel_outbox.enqueue_outbox", AsyncMock()) as enqueue,
    ):
        await channel_notify.send_channel_message("teams:user-1", PICKER)
        proactive.assert_awaited_once()
        enqueue.assert_not_awaited()
