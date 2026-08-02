"""Ensure review always reaches human APPROVE when code changes exist."""

from agent.agents.graph import _route_after_review
from agent.agents.reviewer import _soften_false_incomplete_html


"""Ensure review always reaches human APPROVE when code changes exist."""

from agent.agents.graph import _route_after_review
from agent.agents.reviewer import _soften_false_incomplete_html


def test_approved_goes_to_human_approval_legacy(monkeypatch):
    monkeypatch.setattr("agent.agents.graph.multi_gate_enabled", lambda: False)
    assert (
        _route_after_review(
            {
                "review_result": "approved",
                "review_iteration": 1,
                "file_changes": [{"path": "a.html", "content": "<html></html>"}],
            }
        )
        == "notify_then_approve"
    )


def test_approved_goes_to_pr_publish_when_multi_gate(monkeypatch):
    monkeypatch.setattr("agent.agents.graph.multi_gate_enabled", lambda: True)
    assert (
        _route_after_review(
            {
                "review_result": "approved",
                "review_iteration": 1,
                "file_changes": [{"path": "a.html", "content": "<html></html>"}],
            }
        )
        == "publish_pr"
    )


def test_max_iterations_still_asks_human_approval_legacy(monkeypatch):
    monkeypatch.setattr("agent.agents.graph.multi_gate_enabled", lambda: False)
    assert (
        _route_after_review(
            {
                "review_result": "changes_requested",
                "review_iteration": 3,
                "file_changes": [{"path": "a.html", "content": "<html></html>"}],
            }
        )
        == "notify_then_approve"
    )


def test_max_iterations_multi_gate_publishes_pr(monkeypatch):
    monkeypatch.setattr("agent.agents.graph.multi_gate_enabled", lambda: True)
    assert (
        _route_after_review(
            {
                "review_result": "changes_requested",
                "review_iteration": 3,
                "file_changes": [{"path": "a.html", "content": "<html></html>"}],
            }
        )
        == "publish_pr"
    )


def test_changes_requested_loops_before_max():
    assert (
        _route_after_review(
            {
                "review_result": "changes_requested",
                "review_iteration": 1,
                "file_changes": [{"path": "a.html", "content": "<html></html>"}],
            }
        )
        == "coding_developer"
    )


def test_failed_or_empty_skips_approval():
    assert (
        _route_after_review(
            {
                "status": "failed",
                "review_result": "approved",
                "file_changes": [{"path": "a.html"}],
            }
        )
        == "notify_result"
    )
    assert (
        _route_after_review(
            {"review_result": "approved", "review_iteration": 1, "file_changes": []}
        )
        == "notify_result"
    )


def test_soften_truncation_only_html_issues():
    review = {
        "result": "changes_requested",
        "score": 7,
        "summary": "Looks good but incomplete",
        "issues": [
            {
                "severity": "major",
                "file": "portal.html",
                "description": "The provided HTML diff is incomplete and cuts off mid-line",
            }
        ],
    }
    changes = [
        {
            "path": "src/agent/web/portal.html",
            "content": "<!DOCTYPE html><html><body>ok</body></html>",
        }
    ]
    out = _soften_false_incomplete_html(review, changes)
    assert out["result"] == "approved"
    assert out["issues"] == []
