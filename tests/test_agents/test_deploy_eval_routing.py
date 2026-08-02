"""Graph routing — defer Final evaluation while AzDO CI is watching."""

from agent.agents.graph import _route_after_notify_result
from langgraph.graph import END


def test_notify_result_skips_evaluator_when_pipeline_watching():
    assert _route_after_notify_result({"pipeline_status": "watching"}) == END


def test_notify_result_runs_evaluator_when_not_watching():
    assert _route_after_notify_result({"pipeline_status": "succeeded"}) == "evaluator"
    assert _route_after_notify_result({}) == "evaluator"
