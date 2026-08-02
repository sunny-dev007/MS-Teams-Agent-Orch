"""Regression: config must use pydantic-settings (CI installs pydantic v2)."""


def test_config_imports_and_has_core_settings():
    from agent.config import settings

    assert settings.azure_openai_deployment
    assert hasattr(settings, "effective_api_key")
    assert hasattr(settings, "whatsapp_verify_token")
    assert hasattr(settings, "enable_multi_gate_workflow")
