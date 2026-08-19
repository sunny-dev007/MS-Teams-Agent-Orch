"""Data Analyst specialist wrapper."""

from agent.agents.data_analyst_agent import analyze_excel
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "data_analyst_agent"


async def run_data_analyst(state: AgentState) -> AgentState:
    return tag(await analyze_excel(state), AGENT_NAME)
