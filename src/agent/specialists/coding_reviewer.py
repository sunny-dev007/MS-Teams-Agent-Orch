"""Coding reviewer specialist — review only."""

from agent.agents.reviewer import review_code
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "coding_reviewer"


class CodingReviewerAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        return tag(await review_code(state), AGENT_NAME)


coding_reviewer = CodingReviewerAgent()


async def run_reviewer(state: AgentState) -> AgentState:
    return await coding_reviewer.run(state)
