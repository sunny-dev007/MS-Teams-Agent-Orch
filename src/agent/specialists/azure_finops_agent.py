"""Azure FinOps specialist wrapper."""

from agent.agents.azure_finops_agent import run_azure_finops
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "azure_finops_agent"


async def run_azure_finops_agent(state: AgentState) -> AgentState:
    return tag(await run_azure_finops(state), AGENT_NAME)
