"""Evaluator specialist — final step-by-step report only."""

from agent.agents.evaluator import evaluate_result, task_status
from agent.agents.state import AgentState
from agent.specialists.base import tag

AGENT_NAME = "evaluator_agent"


class EvaluatorAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        return tag(await evaluate_result(state), AGENT_NAME)

    async def status(self, state: AgentState) -> AgentState:
        return tag(await task_status(state), AGENT_NAME)


evaluator_agent = EvaluatorAgent()


async def run_evaluator(state: AgentState) -> AgentState:
    return await evaluator_agent.run(state)


async def run_task_status(state: AgentState) -> AgentState:
    return await evaluator_agent.status(state)
