"""Azure Boards specialist wrapper."""

from agent.agents.boards_agent import list_my_boards
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "boards_agent"


async def run_boards(state: AgentState) -> AgentState:
    return tag(await list_my_boards(state), AGENT_NAME)
