"""Calendar specialist — meeting scheduling only."""

from agent.agents.meeting import schedule_meeting
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "calendar_agent"


class CalendarAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        return tag(await schedule_meeting(state), AGENT_NAME)


calendar_agent = CalendarAgent()


async def run_calendar(state: AgentState) -> AgentState:
    return await calendar_agent.run(state)
