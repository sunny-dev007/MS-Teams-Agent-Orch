"""Doc Upload specialist wrapper."""

from agent.agents.doc_upload_agent import upload_and_ingest_documents
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "doc_upload_agent"


async def run_doc_upload(state: AgentState) -> AgentState:
    return tag(await upload_and_ingest_documents(state), AGENT_NAME)
