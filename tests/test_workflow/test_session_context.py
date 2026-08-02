from agent.workflow.gates import (
    WIZARD_PROVIDER,
    is_conversation_awaiting,
    is_workflow_gate,
    is_wizard_awaiting,
)
from agent.workflow.resume_context import (
    format_gate_hint,
    format_session_cleared,
    format_wizard_hint,
    has_active_gate,
    is_cancel_session_message,
    is_restart_session_message,
    should_continue_conversation,
)
from agent.workflow.gates import GATE_PLAN


def test_provider_is_not_deploy_gate():
    assert not is_workflow_gate(WIZARD_PROVIDER)
    assert not has_active_gate({"awaiting": WIZARD_PROVIDER})
    assert is_wizard_awaiting(WIZARD_PROVIDER)
    assert is_conversation_awaiting(WIZARD_PROVIDER)


def test_plan_is_deploy_gate():
    assert is_workflow_gate(GATE_PLAN)
    assert has_active_gate({"awaiting": GATE_PLAN})


def test_wizard_hint_for_provider():
    text = format_wizard_hint(WIZARD_PROVIDER, {}, "")
    assert "GitHub" in text
    assert "*1*" in text


def test_gate_hint_not_workflow_paused_for_provider():
    text = format_gate_hint(WIZARD_PROVIDER, {}, "")
    assert "Workflow paused" not in text
    assert "Continue setup" in text


def test_cancel_and_restart_patterns():
    assert is_cancel_session_message("stop my previous task")
    assert is_cancel_session_message("new task")
    assert is_cancel_session_message("I want new task")
    assert is_restart_session_message("check my repos")


def test_session_cleared_message():
    text = format_session_cleared(had_task_id="abc", had_gate=GATE_PLAN)
    assert "abc" in text
    assert "check my repos" in text


def test_should_continue_conversation():
    assert should_continue_conversation({"awaiting": "repo"})
    assert not should_continue_conversation({"awaiting": GATE_PLAN})
