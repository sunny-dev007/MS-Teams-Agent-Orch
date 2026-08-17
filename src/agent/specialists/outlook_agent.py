"""Outlook specialist wrapper."""

from agent.agents.outlook_agent import read_outlook
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "outlook_agent"


async def run_outlook(state: AgentState) -> AgentState:
    return tag(await read_outlook(state), AGENT_NAME)
