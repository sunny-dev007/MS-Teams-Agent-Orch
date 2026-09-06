"""Orbit must never stay silent after WORKING_ACK (production silence regression)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.api import teams_bot


@pytest.mark.asyncio
async def test_orbit_sends_status_style_reply_after_ack():
    """Previously dropped after ack → Teams looked dead. Must always send."""
    turn = MagicMock()
    turn.activity = SimpleNamespace(
        text="hello",
        entities=None,
        from_property=SimpleNamespace(aad_object_id="user-1", id="user-1", name="Sunny"),
        conversation=SimpleNamespace(id="conv-1"),
        attachments=None,
    )
    turn.send_activity = AsyncMock()

    MessageFactory = MagicMock()
    MessageFactory.text = MagicMock(side_effect=lambda t: SimpleNamespace(text=t, text_format=None))
    CardFactory = MagicMock()

    result = SimpleNamespace(
        reply=(
            "Got it — I'm working on that.\n"
            "Say **status** in a moment if you need the latest update."
        ),
        adaptive_card=None,
    )

    with (
        patch(
            "agent.api.copilot._handle_copilot_message",
            AsyncMock(return_value=result),
        ),
        patch(
            "agent.services.teams_proactive.save_conversation_reference",
            AsyncMock(),
        ),
        patch(
            "agent.core.channel_outbox.drain_outbox",
            AsyncMock(return_value=[]),
        ),
        patch("agent.api.teams_bot.is_teams_user_allowed", return_value=True),
    ):
        bot = teams_bot.OrbitTeamsBot()
        await bot._handle_message(turn, MessageFactory, CardFactory)

    assert turn.send_activity.await_count >= 2
    texts = [(c.args[0].text if c.args else "") for c in turn.send_activity.await_args_list]
    assert any("working on it" in (t or "").lower() for t in texts)
    assert any("status" in (t or "").lower() for t in texts[1:])


@pytest.mark.asyncio
async def test_orbit_empty_pipeline_still_replies():
    turn = MagicMock()
    turn.activity = SimpleNamespace(
        text="ping",
        entities=None,
        from_property=SimpleNamespace(aad_object_id="user-1", id="user-1", name="Sunny"),
        conversation=SimpleNamespace(id="conv-1"),
        attachments=None,
    )
    turn.send_activity = AsyncMock()
    MessageFactory = MagicMock()
    MessageFactory.text = MagicMock(side_effect=lambda t: SimpleNamespace(text=t, text_format=None))
    CardFactory = MagicMock()
    result = SimpleNamespace(reply="", adaptive_card=None)

    with (
        patch("agent.api.copilot._handle_copilot_message", AsyncMock(return_value=result)),
        patch("agent.services.teams_proactive.save_conversation_reference", AsyncMock()),
        patch("agent.core.channel_outbox.drain_outbox", AsyncMock(return_value=[])),
        patch("agent.api.teams_bot.is_teams_user_allowed", return_value=True),
    ):
        await teams_bot.OrbitTeamsBot()._handle_message(turn, MessageFactory, CardFactory)

    assert turn.send_activity.await_count >= 2
    final = turn.send_activity.await_args_list[-1].args[0].text.lower()
    assert "still working" in final or "status" in final or "help" in final


def test_status_poll_hint_no_longer_blocks_delivery_path():
    """Guard: silence bug was `if sent_ack and _is_status_poll_hint: return`."""
    src = open(teams_bot.__file__, encoding="utf-8").read()
    assert "never stay silent" in src.lower() or "never silent" in src.lower() or "Root cause of production silence" in src
    # The early-return swallow must not exist anymore.
    assert "staying quiet" not in src
    assert "no additional content after ack" not in src


def test_diagnostics_helpers_exist():
    assert callable(teams_bot._record_turn)
    teams_bot._record_turn(ok=False, error="unit-test")
    assert any(e.get("error") == "unit-test" for e in teams_bot._turn_events)
