"""Meeting Plan specialist wrapper."""

from agent.agents import meeting_plan_agent as plan
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "meeting_plan_agent"


async def run_meeting_plan(state: AgentState) -> AgentState:
    return tag(await plan.run_meeting_plan(state), AGENT_NAME)
