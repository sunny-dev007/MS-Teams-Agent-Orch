"""Coding deployer specialist — push/PR/pipeline only (after human approval)."""

from agent.agents.deployment import deploy_code
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "coding_deployer"


class CodingDeployerAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        return tag(await deploy_code(state), AGENT_NAME)


coding_deployer = CodingDeployerAgent()


async def run_deployer(state: AgentState) -> AgentState:
    return await coding_deployer.run(state)
