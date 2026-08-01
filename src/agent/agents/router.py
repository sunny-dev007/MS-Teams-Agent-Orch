"""Backward-compatible shim — planner owns routing now."""

from agent.planner.agent import is_simple_greeting, plan, route_input

__all__ = ["is_simple_greeting", "plan", "route_input"]
