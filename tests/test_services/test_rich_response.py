"""Tests for rich response formatting (WhatsApp + Teams)."""

from agent.services.rich_response import (
    detect_channel,
    format_result_card,
    format_score_metrics,
    format_task_table,
    kv_block,
    metric_bar,
)


def test_metric_bar_bounds():
    text = metric_bar("Score", 8, maximum=10)
    assert "Score" in text
    assert "8/10" in text
    assert "█" in text
    assert "░" in text


def test_kv_block_whatsapp_bullets():
    text = kv_block([("Task", "abc"), ("Status", "ok")], channel="whatsapp")
    assert "• *Task:* abc" in text
    assert "|" not in text


def test_kv_block_teams_markdown_table():
    text = kv_block([("Task", "abc"), ("Status", "ok")], channel="teams")
    assert "| Field | Value |" in text
    assert "| Task | abc |" in text


def test_detect_channel():
    assert detect_channel("teams:user@x.com") == "teams"
    assert detect_channel("+15551212") == "whatsapp"


def test_result_card_includes_metrics_and_next():
    text = format_result_card(
        title="Deployment completed (`t1`)",
        ok=True,
        fields=[("Pipeline", "succeeded"), ("Live portal", "https://x/portal")],
        metrics=[("Pipeline", 10, 10)],
        actions=["Open Live portal"],
        channel="whatsapp",
    )
    assert "Deployment completed" in text
    assert "[OK]" in text
    assert "Metrics" in text
    assert "Next" in text
    assert "portal" in text.lower()


def test_task_table_teams_has_header_row():
    text = format_task_table(
        [{"id": "abc12345", "status": "completed", "intent": "code_change"}],
        channel="teams",
    )
    assert "| Task | Status | Intent |" in text
    assert "abc12345" in text


def test_score_metrics():
    text = format_score_metrics({"Quality": 9, "Security": 7}, maximum=10)
    assert "Quality" in text
    assert "Security" in text
