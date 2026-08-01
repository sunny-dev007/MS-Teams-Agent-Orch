"""Notifier specialist — WhatsApp send only."""

from agent.agents.notification import notify
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "notifier_agent"


class NotifierAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        return tag(await notify(state), AGENT_NAME)


notifier_agent = NotifierAgent()


async def run_notifier(state: AgentState) -> AgentState:
    return await notifier_agent.run(state)
