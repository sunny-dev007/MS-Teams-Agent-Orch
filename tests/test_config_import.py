"""Regression: config must use pydantic-settings (CI installs pydantic v2)."""

from pathlib import Path


def test_config_source_forbids_pydantic_v1_basesettings():
    src = Path("src/agent/config.py").read_text(encoding="utf-8")
    assert "from pydantic import BaseSettings" not in src
    assert "from pydantic_settings import BaseSettings" in src
    assert "SettingsConfigDict" in src


def test_config_imports_and_has_core_settings():
    from agent.config import settings

    assert settings.azure_openai_deployment
    assert hasattr(settings, "effective_api_key")
    assert hasattr(settings, "whatsapp_verify_token")
    assert hasattr(settings, "enable_multi_gate_workflow")
    assert hasattr(settings, "database_url")
    assert hasattr(settings, "azdo_org_url")


def test_allowed_teams_user_ids_accepts_plain_uuid(monkeypatch):
    """App Service often stores a single OID without JSON brackets — must not crash boot."""
    monkeypatch.setenv("ALLOWED_TEAMS_USER_IDS", "f6b50e54-1aab-4373-bc48-f4cf93810be9")
    from agent.config import Settings

    parsed = Settings().allowed_teams_user_ids
    assert parsed == ["f6b50e54-1aab-4373-bc48-f4cf93810be9"]


def test_allowed_teams_user_ids_accepts_json_array(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_TEAMS_USER_IDS",
        '["user-a","user-b"]',
    )
    from agent.config import Settings

    assert Settings().allowed_teams_user_ids == ["user-a", "user-b"]
