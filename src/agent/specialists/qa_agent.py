"""QA specialist wrapper."""

from agent.agents.qa_agent import run_qa_smoke
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "qa_agent"


async def run_qa(state: AgentState) -> AgentState:
    return tag(await run_qa_smoke(state), AGENT_NAME)
