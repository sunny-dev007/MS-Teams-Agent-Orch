"""Docs specialist wrapper."""

from agent.agents.docs_agent import publish_release_notes
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "docs_agent"


async def run_docs(state: AgentState) -> AgentState:
    return tag(await publish_release_notes(state), AGENT_NAME)
