"""Tests for architect heuristic plan fallback."""

from agent.agents.architect import _format_plan_fallback, _heuristic_plan


def test_heuristic_plan_portal_dark_version():
    tree = "- src/agent/static/portal.html\n- README.md"
    plan = _heuristic_plan(
        "redesign portal page with dark layout and show deployed version",
        tree,
        "https://example.com/repo",
    )
    assert plan.get("_heuristic") is True
    assert "portal" in plan["summary"].lower() or "dark" in plan["approach"].lower()
    files = plan.get("files_to_modify") or []
    assert any("portal" in str(f).lower() for f in files)
    detail = plan.get("whatsapp_detail") or _format_plan_fallback(plan)
    assert "Summary" in detail
    assert "429" not in detail
