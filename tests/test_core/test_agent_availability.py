"""Graceful offline-agent copy when ENABLE_* flags are false."""

from agent.core.agent_availability import (
    azure_finops_offline,
    format_agent_offline,
    outlook_offline,
)


def test_format_agent_offline_mentions_enable_and_ask_again():
    text = format_agent_offline(
        "Demo Agent",
        env_flag="ENABLE_DEMO_AGENT",
        try_again="try demo",
        meanwhile="help",
    )
    low = text.lower()
    assert "turned off" in low
    assert "ENABLE_DEMO_AGENT=true" in text
    assert "same question" in low
    assert "help" in low


def test_outlook_and_finops_presets():
    o = outlook_offline()
    assert "Outlook" in o
    assert "ENABLE_OUTLOOK_AGENT=true" in o
    f = azure_finops_offline()
    assert "FinOps" in f
    assert "ENABLE_AZURE_FINOPS_AGENT=true" in f
