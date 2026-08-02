from agent.agents.pr_publisher import publish_pull_request
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "pr_publisher"


async def run_pr_publisher(state: AgentState) -> AgentState:
    return tag(await publish_pull_request(state), AGENT_NAME)
