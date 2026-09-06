"""Orbit turn-local delivery helpers.

During an active Teams Bot turn we deliver agent replies through the Bot
Framework turn (or outbox drained into that turn). Proactive
continue_conversation is only for late/background updates after the turn ends.

IMPORTANT: asyncio.create_task copies ContextVars. A plain bool ContextVar
stays True in child tasks after the parent exits the context manager, which
blocks proactive delivery forever. We use a live turn-id identity check so
background tasks stop seeing "in turn" once the HTTP turn ends.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_orbit_turn_id: ContextVar[Any | None] = ContextVar("orbit_turn_id", default=None)
_live_turn_id: Any | None = None


def is_orbit_turn_active() -> bool:
    turn = _orbit_turn_id.get()
    return turn is not None and turn is _live_turn_id


@contextmanager
def orbit_turn():
    """Mark the current task as inside an Orbit Teams bot turn."""
    global _live_turn_id
    turn_id = object()
    _live_turn_id = turn_id
    token = _orbit_turn_id.set(turn_id)
    try:
        yield
    finally:
        if _live_turn_id is turn_id:
            _live_turn_id = None
        _orbit_turn_id.reset(token)
