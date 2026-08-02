from agent.services.deploy_notify import (
    format_merge_failed,
    format_merging_pr,
    user_facing_deploy_error,
)


def test_merging_message_includes_task_and_pr():
    text = format_merging_pr("f2cf084a", "https://dev.azure.com/pr/34", 34)
    assert "f2cf084a" in text
    assert "PR #34" in text
    assert "Merging" in text


def test_user_facing_deploy_error_hides_raw_exception():
    msg = user_facing_deploy_error(Exception("Error 429 rate_limit_exceeded"), step="merge")
    assert "429" not in msg
    assert "merge" in msg.lower()


def test_merge_failed_includes_retry_hint():
    text = format_merge_failed("abc", "https://pr", "conflict on main")
    assert "APPROVE abc" in text
    assert "conflict" in text.lower()
