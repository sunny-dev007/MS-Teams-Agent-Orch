"""Smoke tests for Orbit Teams bot channel (offline, no Azure calls)."""

from __future__ import annotations

from agent.api import teams_bot
from agent.config import Settings


def test_teams_bot_disabled_by_default():
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.enable_teams_bot_channel is False


def test_teams_bot_enabled_requires_app_id(monkeypatch):
    monkeypatch.setattr(teams_bot.settings, "enable_teams_bot_channel", True)
    monkeypatch.setattr(teams_bot.settings, "microsoft_app_id", "")
    assert teams_bot.teams_bot_enabled() is False
    monkeypatch.setattr(teams_bot.settings, "microsoft_app_id", "11111111-1111-1111-1111-111111111111")
    assert teams_bot.teams_bot_enabled() is True


def test_teams_bot_routes_registered():
    paths = {getattr(r, "path", None) for r in teams_bot.router.routes}
    assert "/api/channels/teamsbot/messages" in paths
    assert "/api/channels/teamsbot/health" in paths
