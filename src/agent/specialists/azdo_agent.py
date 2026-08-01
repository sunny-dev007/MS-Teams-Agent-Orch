"""Azure DevOps specialist — projects/repos/PR/pipeline only (no GitHub)."""

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.services import azure_devops as azdo
from agent.specialists.base import tag

logger = get_logger(__name__)
AGENT_NAME = "azdo_agent"


class AzdoAgent:
    name = AGENT_NAME

    async def list_projects(self) -> list[dict]:
        if not settings.azdo_pat.get_secret_value():
            raise RuntimeError("AZDO_PAT is not configured")
        return await azdo.list_projects()

    async def list_repositories(self, project: str) -> list[dict]:
        return await azdo.list_repositories(project)

    async def run(self, state: AgentState) -> AgentState:
        try:
            projects = await self.list_projects()
        except Exception as e:
            logger.exception("%s list projects failed", AGENT_NAME)
            return tag(
                {
                    **state,
                    "status": "failed",
                    "notification_text": f"Azure DevOps agent error: {e}",
                },
                AGENT_NAME,
            )
        slim = [{"id": p.get("id"), "name": p.get("name")} for p in projects]
        lines = [f"{i}. *{p['name']}*" for i, p in enumerate(slim, 1)]
        return tag(
            {
                **state,
                "status": "repo_picker",
                "repo_provider": "azure_devops",
                "session_data": {**(state.get("session_data") or {}), "projects": slim},
                "notification_text": "*Azure DevOps projects*\n\n"
                + "\n".join(lines)
                + "\n\nReply with a number.",
            },
            AGENT_NAME,
        )


azdo_agent = AzdoAgent()
