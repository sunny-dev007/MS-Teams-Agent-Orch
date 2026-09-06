"""Regression: post-deploy verify must treat empty Teams allowlist as open mode."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "infra" / "verify_post_deploy.sh"


def _first_allowlisted(raw: str) -> str:
    out = subprocess.check_output(
        [
            "bash",
            "-c",
            f'source "{SCRIPT}"; first_allowlisted_user "$1"',
            "_",
            raw,
        ],
        env=os.environ.copy(),
        text=True,
    )
    return out.strip()


def test_verify_script_open_mode_contract():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "never fail the pipeline solely because it is empty" in text
    assert "cannot verify authorization" not in text
    assert "pipeline-verify-user" in text
    assert "OPEN_MODE=1" in text
    assert '[[ "${BASH_SOURCE[0]}" == "${0}" ]]' in text


def test_first_allowlisted_user_empty_is_ok():
    assert _first_allowlisted("") == ""
    assert _first_allowlisted("   ") == ""


def test_first_allowlisted_user_csv_and_json():
    assert _first_allowlisted("alice,bob") == "alice"
    assert _first_allowlisted('["oid-1", "oid-2"]') == "oid-1"


def test_security_empty_allowlist_allows_all(monkeypatch):
    from agent.config import settings
    from agent.core.security import is_teams_user_allowed

    monkeypatch.setattr(settings, "allowed_teams_user_ids", [])
    assert is_teams_user_allowed("anyone") is True

    monkeypatch.setattr(settings, "allowed_teams_user_ids", ["real-user"])
    assert is_teams_user_allowed("anyone") is False
    assert is_teams_user_allowed("real-user") is True
