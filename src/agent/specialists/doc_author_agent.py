"""Doc Author specialist wrapper."""

from agent.agents import doc_author_agent as author
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "doc_author_agent"


async def run_doc_author(state: AgentState) -> AgentState:
    return tag(await author.run_doc_author(state), AGENT_NAME)
