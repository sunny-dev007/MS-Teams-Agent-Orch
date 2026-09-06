"""Orbit outbox flusher — auto-push without user typing status."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.services import orbit_delivery


@pytest.mark.asyncio
async def test_flush_skips_when_teams_bot_disabled(monkeypatch):
    monkeypatch.setattr(
        "agent.services.orbit_delivery.settings.enable_teams_bot_channel", False
    )
    result = await orbit_delivery.flush_orbit_outbox_once()
    assert result.get("skipped") is True


@pytest.mark.asyncio
async def test_flush_pushes_and_marks_delivered(monkeypatch):
    monkeypatch.setattr(
        "agent.services.orbit_delivery.settings.enable_teams_bot_channel", True
    )
    monkeypatch.setattr(
        "agent.services.orbit_delivery.settings.enable_orbit_outbox_flusher", True
    )
    with (
        patch(
            "agent.core.channel_outbox.list_pending_outbox_sessions",
            AsyncMock(return_value=["teams:u1"]),
        ),
        patch(
            "agent.core.channel_outbox.peek_outbox_texts",
            AsyncMock(return_value=[(11, "PR opened"), (12, "Review ready")]),
        ),
        patch(
            "agent.core.channel_outbox.mark_outbox_ids_delivered",
            AsyncMock(return_value=2),
        ) as mark,
        patch(
            "agent.services.teams_proactive.try_send_proactive_teams_message",
            AsyncMock(return_value=True),
        ) as send,
    ):
        result = await orbit_delivery.flush_orbit_outbox_once(min_age_seconds=0)
    assert result["pushed"] == 2
    assert send.await_count == 2
    mark.assert_awaited_once_with([11, 12])


@pytest.mark.asyncio
async def test_flush_stops_on_first_failure(monkeypatch):
    monkeypatch.setattr(
        "agent.services.orbit_delivery.settings.enable_teams_bot_channel", True
    )
    monkeypatch.setattr(
        "agent.services.orbit_delivery.settings.enable_orbit_outbox_flusher", True
    )
    with (
        patch(
            "agent.core.channel_outbox.list_pending_outbox_sessions",
            AsyncMock(return_value=["teams:u1"]),
        ),
        patch(
            "agent.core.channel_outbox.peek_outbox_texts",
            AsyncMock(return_value=[(1, "a"), (2, "b")]),
        ),
        patch(
            "agent.core.channel_outbox.mark_outbox_ids_delivered",
            AsyncMock(return_value=0),
        ) as mark,
        patch(
            "agent.services.teams_proactive.try_send_proactive_teams_message",
            AsyncMock(return_value=False),
        ),
    ):
        result = await orbit_delivery.flush_orbit_outbox_once(min_age_seconds=0)
    assert result["pushed"] == 0
    assert result["failed"] == 1
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_teams_notify_retries_then_outbox(monkeypatch):
    from agent.services import channel_notify, teams_proactive

    with (
        patch.object(
            teams_proactive,
            "load_conversation_reference",
            AsyncMock(return_value={"service_url": "https://x", "conversation": {"id": "c"}}),
        ),
        patch.object(
            teams_proactive,
            "try_send_proactive_teams_message",
            AsyncMock(return_value=False),
        ) as proactive,
        patch("agent.core.channel_outbox.enqueue_outbox", AsyncMock()) as enqueue,
        patch("asyncio.sleep", AsyncMock()),
    ):
        await channel_notify.send_channel_message("teams:retry-user", "Development complete")
    assert proactive.await_count == 3
    enqueue.assert_awaited_once()
