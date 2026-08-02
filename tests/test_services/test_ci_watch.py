import pytest

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


@pytest.mark.asyncio
async def test_get_latest_skips_old_build_before_watch(monkeypatch):
    """Do not treat a previous completed build as the merge result."""
    from agent.services import ci_watch

    watch_started = 1_700_000_000.0

    async def fake_list_builds(*_a, **_k):
        return [
            {
                "id": 99,
                "result": "succeeded",
                "finishTime": "2023-11-14T10:00:00Z",
                "sourceVersion": "abc123deadbeef",
                "repository": {"name": "web.Whatsapp-AI-Agent", "id": "r1"},
                "definition": {"name": "web.Whatsapp-AI-Agent"},
            },
            {
                "id": 100,
                "result": "failed",
                "finishTime": "2023-11-15T12:00:00Z",
                "sourceVersion": "dd0139fa9999",
                "repository": {"name": "web.Whatsapp-AI-Agent", "id": "r1"},
                "definition": {"name": "web.Whatsapp-AI-Agent"},
            },
        ]

    monkeypatch.setattr("agent.services.azure_devops.list_builds", fake_list_builds)

    matched = await ci_watch.get_latest_azdo_build_result(
        "Project-NIT",
        repo_name="web.Whatsapp-AI-Agent",
        repo_id="r1",
        commit_sha="dd0139fa9999",
        min_created_at=watch_started,
        require_sha_match=True,
    )
    assert matched is not None
    assert matched.build_id == "100"
    assert matched.outcome == "failed"

    stale = await ci_watch.get_latest_azdo_build_result(
        "Project-NIT",
        repo_name="web.Whatsapp-AI-Agent",
        repo_id="r1",
        commit_sha="abc123deadbeef",
        min_created_at=watch_started + 86400,
        require_sha_match=True,
    )
    assert stale is None

