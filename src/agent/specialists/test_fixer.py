"""Test-fixer specialist — repairs CI pytest failures after WhatsApp approval."""

from agent.agents.state import AgentState
from agent.agents.test_fixer import fix_ci_test_failures
from agent.specialists.base import tag

AGENT_NAME = "test_fixer"


class TestFixerAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        return tag(await fix_ci_test_failures(state), AGENT_NAME)


test_fixer = TestFixerAgent()


async def run_test_fixer(state: AgentState) -> AgentState:
    return await test_fixer.run(state)
