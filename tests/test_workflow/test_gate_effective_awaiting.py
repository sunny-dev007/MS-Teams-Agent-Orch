"""Gate routing — stale plan_approval must not block PR AI review."""

from agent.api.whatsapp import PR_AI_PATTERN, PR_MANUAL_PATTERN, PROCEED_PATTERN
from agent.workflow.gates import (
    GATE_PLAN,
    GATE_PR_MODE,
    effective_awaiting,
    session_has_pr,
)


def test_effective_awaiting_upgrades_stale_plan_when_pr_exists():
    session = {
        "awaiting": GATE_PLAN,
        "data": {
            "pending_task_id": "28e0e1f7",
            "pr_url": "https://dev.azure.com/x/_git/r/pullrequest/36",
            "pr_id": 36,
        },
    }
    assert session_has_pr(session["data"])
    assert effective_awaiting(session) == GATE_PR_MODE


def test_effective_awaiting_keeps_plan_without_pr():
    session = {
        "awaiting": GATE_PLAN,
        "data": {"pending_task_id": "28e0e1f7"},
    }
    assert effective_awaiting(session) == GATE_PLAN


def test_pr_ai_choice_patterns():
    assert PR_AI_PATTERN.match("1")
    assert PR_AI_PATTERN.match("AI REVIEW")
    assert PR_MANUAL_PATTERN.match("2")
    assert not PROCEED_PATTERN.match("1")


def test_stale_plan_plus_one_should_route_as_pr_mode():
    """Mirrors the WhatsApp bug: user replied 1 after PR opened, session still plan_approval."""
    session = {
        "awaiting": GATE_PLAN,
        "data": {
            "pending_task_id": "28e0e1f7",
            "pr_url": "https://dev.azure.com/Az-FullStack/Project-NIT/_git/web.Whatsapp-AI-Agent/pullrequest/36",
        },
    }
    gate = effective_awaiting(session)
    has_pr = session_has_pr(session["data"])
    assert gate == GATE_PR_MODE
    assert has_pr
    assert PR_AI_PATTERN.match("1")
    # Webhook condition: (pr_ai_match) and (gate == PR_MODE or has_pr)
    assert gate == GATE_PR_MODE or has_pr
