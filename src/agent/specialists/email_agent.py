"""Email specialist — Gmail read/compose only."""

from agent.agents.gmail_agent import compose_and_send_email, read_gmail
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "email_agent"


class EmailAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        intent = state.get("intent", "")
        if intent == "send_email":
            result = await compose_and_send_email(state)
        else:
            result = await read_gmail(state)
        return tag(result, AGENT_NAME)


email_agent = EmailAgent()


async def run_email(state: AgentState) -> AgentState:
    return await email_agent.run(state)


async def run_send_email(state: AgentState) -> AgentState:
    return tag(await compose_and_send_email(state), AGENT_NAME)
