"""Shared specialist contract."""

from __future__ import annotations

from typing import Protocol

from agent.agents.state import AgentState


class Specialist(Protocol):
    name: str

    async def run(self, state: AgentState) -> AgentState: ...


def tag(state: AgentState, agent_name: str) -> AgentState:
    return {**state, "handled_by": agent_name}
