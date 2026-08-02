import re

from agent.api.whatsapp import (
    APPROVE_PATTERN,
    PR_AI_PATTERN,
    PR_MANUAL_PATTERN,
    PR_READY_PATTERN,
    PROCEED_PATTERN,
    REJECT_PATTERN,
    _task_id_from_match,
)
from agent.workflow.gates import GATE_DEPLOY, GATE_MANUAL_PR, GATE_PLAN, GATE_PR_MODE


def test_proceed_plan_with_task_id():
    m = PROCEED_PATTERN.match("PROCEED abc12345")
    assert _task_id_from_match(m) == "abc12345"


def test_pr_review_mode_patterns():
    assert PR_AI_PATTERN.match("1")
    assert PR_AI_PATTERN.match("AI REVIEW")
    assert PR_MANUAL_PATTERN.match("2")
    assert PR_MANUAL_PATTERN.match("MANUAL REVIEW")


def test_pr_ready_pattern():
    m = PR_READY_PATTERN.match("PR READY deadbeef")
    assert _task_id_from_match(m) == "deadbeef"


def test_deploy_approve_still_works():
    assert APPROVE_PATTERN.match("Approve")
    assert APPROVE_PATTERN.match("APPROVE abc123")


def test_gate_constants():
    assert GATE_PLAN == "plan_approval"
    assert GATE_PR_MODE == "pr_review_mode"
    assert GATE_MANUAL_PR == "manual_pr_review"
    assert GATE_DEPLOY == "approval"


def test_reject_plan():
    assert REJECT_PATTERN.match("REJECT plan123")
