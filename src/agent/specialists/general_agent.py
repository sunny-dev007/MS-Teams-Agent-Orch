"""General conversation specialist — persona replies only."""

import time

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.core.persona import get_persona_prompt
from agent.services.llm import invoke_llm, user_facing_llm_error
from agent.specialists.base import tag

logger = get_logger(__name__)
AGENT_NAME = "general_agent"


class GeneralAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        if state.get("notification_text"):
            return tag({**state, "status": "general_response"}, AGENT_NAME)

        t0 = time.perf_counter()
        try:
            response = await invoke_llm(
                [
                    SystemMessage(
                        content=(
                            get_persona_prompt()
                            + "\n\nAnswer concisely for WhatsApp. Prefer bullets over long paragraphs."
                        )
                    ),
                    HumanMessage(content=state.get("user_message", "Hello")),
                ],
                temperature=0.3,
                role="default",
            )
        except Exception:
            logger.exception("%s LLM failed task=%s", AGENT_NAME, state.get("task_id"))
            return tag(
                {
                    **state,
                    "status": "general_response",
                    "notification_text": user_facing_llm_error("message"),
                },
                AGENT_NAME,
            )
        logger.info(
            "%s reply %.2fs task=%s",
            AGENT_NAME,
            time.perf_counter() - t0,
            state.get("task_id"),
        )
        return tag(
            {
                **state,
                "status": "general_response",
                "notification_text": response.content,
            },
            AGENT_NAME,
        )


general_agent = GeneralAgent()


async def run_general(state: AgentState) -> AgentState:
    return await general_agent.run(state)
