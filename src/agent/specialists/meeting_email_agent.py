"""Meeting Email specialist wrapper."""

from agent.agents import meeting_email_agent as mail
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "meeting_email_agent"


async def run_meeting_email(state: AgentState) -> AgentState:
    return tag(await mail.run_meeting_email(state), AGENT_NAME)
