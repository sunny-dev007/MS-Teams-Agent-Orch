from agent.services.ci_watch import (
    CiTerminalResult,
    _normalize_outcome,
    format_ci_final_evaluation,
    format_ci_status_notification,
)


def test_normalize_outcome_aliases():
    assert _normalize_outcome("succeeded") == "succeeded"
    assert _normalize_outcome("failed") == "failed"
    assert _normalize_outcome("canceled") == "canceled"
    assert _normalize_outcome("cancelled") == "canceled"
    assert _normalize_outcome("partiallySucceeded") == "partiallySucceeded"


def test_final_evaluation_success_mentions_portal():
    watch = {
        "task_id": "abc123",
        "pr_url": "https://dev.azure.com/x/_git/r/pullrequest/1",
        "pipeline_url": "https://dev.azure.com/x/_build",
        "live_url": "https://example.com/portal",
    }
    text = format_ci_final_evaluation(
        watch,
        CiTerminalResult(
            outcome="succeeded",
            url="https://dev.azure.com/x/_build/results?buildId=9",
        ),
    )
    assert "Final evaluation" in text
    assert "*succeeded*" in text
    assert "portal" in text.lower()


def test_final_evaluation_canceled_and_failed():
    watch = {"task_id": "t1", "pipeline_url": "https://p"}
    canceled = format_ci_final_evaluation(
        watch, CiTerminalResult(outcome="canceled", url="https://p")
    )
    failed = format_ci_status_notification(
        watch, CiTerminalResult(outcome="failed", url="https://p", detail="boom")
    )
    assert "canceled" in canceled.lower()
    assert "Deployment failed" in failed


def test_github_path_untouched_by_import():
    """Smoke: GitHub deploy helpers still import independently of AzDO watch."""
    from agent.agents.deployment import _deploy_github  # noqa: F401
    from agent.services.ci_gate import wait_for_ci_idle  # noqa: F401
