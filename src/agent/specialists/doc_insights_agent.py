"""Doc Insights specialist wrapper."""

from agent.agents.doc_insights_agent import summarize_documents
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "doc_insights_agent"


async def run_doc_insights(state: AgentState) -> AgentState:
    return tag(await summarize_documents(state), AGENT_NAME)
