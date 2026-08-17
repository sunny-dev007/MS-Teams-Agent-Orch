"""Doc Library specialist wrapper."""

from agent.agents.doc_library_agent import list_documents
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "doc_library_agent"


async def run_doc_library(state: AgentState) -> AgentState:
    return tag(await list_documents(state), AGENT_NAME)
