"""Doc Ingest specialist wrapper."""

from agent.agents.doc_ingest_agent import ingest_documents
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "doc_ingest_agent"


async def run_doc_ingest(state: AgentState) -> AgentState:
    return tag(await ingest_documents(state), AGENT_NAME)
