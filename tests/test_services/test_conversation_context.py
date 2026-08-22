"""Conversation context stack — pause/resume multi-agent flows."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_pause_and_resume_doc_pick():
    from agent.services.conversation_context import pause_current_context, resume_previous_context

    store: dict = {
        "phone": "teams:user-1",
        "awaiting": "doc_pick",
        "provider": None,
        "data": {"active_workspace": "knowledge", "doc_catalog": [{"pick": 1}]},
    }

    async def fake_get(phone):
        return dict(store)

    async def fake_save(phone, *, awaiting=None, provider=None, data=None, merge_data=True, clear_awaiting=False):
        if clear_awaiting:
            store["awaiting"] = None
        elif awaiting is not None:
            store["awaiting"] = awaiting
        if data is not None:
            if merge_data:
                store["data"] = {**store.get("data", {}), **data}
            else:
                store["data"] = data

    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr("agent.services.conversation_context.get_session", fake_get)
        mp.setattr("agent.services.conversation_context.save_session", fake_save)

        paused = await pause_current_context("teams:user-1", reason="release notes")
        assert paused is not None
        assert paused["awaiting"] == "doc_pick"

        store["awaiting"] = None
        restored = await resume_previous_context("teams:user-1")
        assert restored is not None
        assert restored.get("restored") is True
        assert store.get("awaiting") == "doc_pick"


@pytest.mark.asyncio
async def test_find_release_event_requires_lookup_key():
    from agent.models.release_event import find_release_event

    assert await find_release_event() is None
    assert await find_release_event(pr_id=None, pipeline_id=None, build_id=None) is None
