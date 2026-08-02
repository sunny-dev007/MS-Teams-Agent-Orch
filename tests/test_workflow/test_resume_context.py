from agent.workflow.resume_context import (
    format_gate_hint,
    format_no_pending_task,
    format_plan_rejected,
    format_resume_ack,
    has_active_gate,
    is_resume_status_message,
)
from agent.workflow.gates import GATE_PLAN


def test_resume_status_message():
    assert is_resume_status_message("resume")
    assert is_resume_status_message("where am i")
    assert is_resume_status_message("status")
    assert not is_resume_status_message("check my repos")


def test_gate_hint_plan_includes_proceed():
    text = format_gate_hint(
        GATE_PLAN,
        {
            "pending_task_id": "abc123",
            "repo_name": "personal-task-api",
            "repo_provider": "github",
            "user_message": "add delete endpoint",
        },
    )
    assert "PROCEED abc123" in text
    assert "Gate 1" in text
    assert "GitHub" in text


def test_gate_hint_deploy():
    text = format_gate_hint(
        "approval",
        {"pending_task_id": "x1", "repo_provider": "azure_devops", "repo_name": "web.Whatsapp-AI-Agent"},
    )
    assert "APPROVE x1" in text
    assert "Final evaluation" in text


def test_plan_rejected_copy():
    assert "No code was written" in format_plan_rejected("t1")


def test_resume_ack_plan():
    assert "Starting development" in format_resume_ack(GATE_PLAN, "t1", "approved")


def test_no_pending():
    assert "check my repos" in format_no_pending_task().lower()


def test_has_active_gate():
    assert has_active_gate({"awaiting": "plan_approval"})
    assert not has_active_gate({"awaiting": None})
