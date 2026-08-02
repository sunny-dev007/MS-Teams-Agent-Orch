from agent.agents.architect import plan_implementation
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "coding_architect"


async def run_architect(state: AgentState) -> AgentState:
    return tag(await plan_implementation(state), AGENT_NAME)
