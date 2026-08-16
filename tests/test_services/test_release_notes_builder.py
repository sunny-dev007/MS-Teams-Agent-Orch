"""Unit tests for enterprise release-notes HTML builder (no live AzDO)."""

from agent.services.release_notes_builder import _fallback_sections, render_enterprise_html


def test_render_enterprise_html_includes_sections():
    evidence = {
        "pr_id": "41",
        "pr_title": "Docs polish",
        "project": "Project-NIT",
        "repo_name": "web.Whatsapp-AI-Agent",
        "created_by": "Sunny",
        "reviewers": ["Reviewer A"],
        "source_branch": "agent/x",
        "target_branch": "main",
        "pr_status": "completed",
        "commit_sha": "abc123",
        "pr_url": "https://example/pr/41",
        "commits": ["abc123 Add release notes"],
        "files_changed": ["/docs/RELEASE_NOTES.md", "/README.md"],
        "file_count": 2,
    }
    sections = _fallback_sections(evidence)
    event = {
        "release_id": "rel_test",
        "pipeline_id": "15",
        "build_id": "100",
        "env": "prod",
        "app_url": "https://example.azurewebsites.net",
    }
    html = render_enterprise_html(evidence=evidence, sections=sections, event=event)
    assert "Enterprise Release Notes" in html
    assert "Executive summary" in html
    assert "rel_test" in html
    assert "web.Whatsapp-AI-Agent" in html
    assert "docs/RELEASE_NOTES.md" in html
    assert "write release notes for PR" not in html.lower() or "PR 41" in html
