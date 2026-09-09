"""Orbit turn-local delivery helpers.

During an active Teams Bot turn we deliver agent replies through the Bot
Framework turn (or outbox drained into that turn). Proactive
continue_conversation is only for late/background updates after the turn ends.

IMPORTANT: asyncio.create_task copies ContextVars. A plain bool ContextVar
stays True in child tasks after the parent exits the context manager, which
blocks proactive delivery forever. We use a live turn-id identity check so
background tasks stop seeing "in turn" once the HTTP turn ends.

channel_surface ContextVar marks Orbit vs Copilot Studio so LLM / Doc Author
upgrades stay Orbit-only and do not change AI Dev Agent behaviour.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

_orbit_turn_id: ContextVar[Any | None] = ContextVar("orbit_turn_id", default=None)
_channel_surface: ContextVar[str] = ContextVar("channel_surface", default="")
_live_turn_id: Any | None = None


def is_orbit_turn_active() -> bool:
    turn = _orbit_turn_id.get()
    return turn is not None and turn is _live_turn_id


def get_channel_surface() -> str:
    return (_channel_surface.get() or "").strip().lower()


def is_orbit_surface() -> bool:
    """True for Orbit Teams Bot path (not Copilot Studio AI Dev Agent)."""
    if get_channel_surface() == "orbit":
        return True
    return is_orbit_turn_active()


def set_channel_surface(surface: str) -> Token:
    return _channel_surface.set((surface or "").strip().lower())


def reset_channel_surface(token: Token) -> None:
    _channel_surface.reset(token)


@contextmanager
def channel_surface_scope(surface: str) -> Iterator[None]:
    token = set_channel_surface(surface)
    try:
        yield
    finally:
        reset_channel_surface(token)


@contextmanager
def orbit_turn():
    """Mark the current task as inside an Orbit Teams bot turn."""
    global _live_turn_id
    turn_id = object()
    _live_turn_id = turn_id
    token = _orbit_turn_id.set(turn_id)
    surface_token = set_channel_surface("orbit")
    try:
        yield
    finally:
        if _live_turn_id is turn_id:
            _live_turn_id = None
        _orbit_turn_id.reset(token)
        reset_channel_surface(surface_token)
