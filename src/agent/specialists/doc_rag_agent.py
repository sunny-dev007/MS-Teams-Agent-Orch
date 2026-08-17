"""Doc RAG specialist wrapper."""

from agent.agents.doc_rag_agent import ask_documents
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "doc_rag_agent"


async def run_doc_rag(state: AgentState) -> AgentState:
    return tag(await ask_documents(state), AGENT_NAME)
