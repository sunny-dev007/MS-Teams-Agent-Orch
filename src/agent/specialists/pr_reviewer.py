from agent.agents.pr_reviewer import review_pull_request
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "pr_ai_reviewer"


async def run_pr_reviewer(state: AgentState) -> AgentState:
    return tag(await review_pull_request(state), AGENT_NAME)
