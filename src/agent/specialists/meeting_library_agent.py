"""Meeting Library specialist wrapper."""

from agent.agents import meeting_library_agent as lib
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "meeting_library_agent"


async def run_meeting_library(state: AgentState) -> AgentState:
    return tag(await lib.run_meeting_library(state), AGENT_NAME)
