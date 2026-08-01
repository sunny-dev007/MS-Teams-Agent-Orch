"""Coding developer specialist — clone + codegen only."""

from agent.agents.developer import develop_code
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "coding_developer"


class CodingDeveloperAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        return tag(await develop_code(state), AGENT_NAME)


coding_developer = CodingDeveloperAgent()


async def run_developer(state: AgentState) -> AgentState:
    return await coding_developer.run(state)
