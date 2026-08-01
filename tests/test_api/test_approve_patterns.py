import re

from agent.api.whatsapp import APPROVE_PATTERN, REJECT_PATTERN, _task_id_from_match
from agent.agents.graph import _format_change_preview


def test_approve_with_task_id():
    m = APPROVE_PATTERN.match("APPROVE a71b354f")
    assert _task_id_from_match(m) == "a71b354f"


def test_approve_bare():
    m = APPROVE_PATTERN.match("Approve")
    assert m is not None
    assert _task_id_from_match(m) is None


def test_final_approval_phrases():
    assert APPROVE_PATTERN.match("final approval")
    assert APPROVE_PATTERN.match("final approval for deployment")
    assert APPROVE_PATTERN.match("approve deploy")
    assert APPROVE_PATTERN.match("deploy now")
    assert _task_id_from_match(APPROVE_PATTERN.match("approve deploy abc123")) == "abc123"


def test_approve_with_punctuation():
    m = APPROVE_PATTERN.match("approve a71b354f!")
    assert _task_id_from_match(m) == "a71b354f"


def test_reject_bare_and_with_id():
    assert _task_id_from_match(REJECT_PATTERN.match("Reject")) is None
    assert _task_id_from_match(REJECT_PATTERN.match("REJECT abc123")) == "abc123"


def test_non_approve_does_not_match():
    assert APPROVE_PATTERN.match("please approve later") is None
    assert APPROVE_PATTERN.match("check my repos") is None


def test_change_preview_includes_snippet():
    text = _format_change_preview(
        [{"action": "modify", "path": "main.py", "content": "def delete():\n    pass\n"}]
    )
    assert "main.py" in text
    assert "def delete" in text
