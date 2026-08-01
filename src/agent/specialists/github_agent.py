"""GitHub specialist — list/inspect GitHub repos only (no AzDO)."""

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.services import github as gh
from agent.specialists.base import tag

logger = get_logger(__name__)
AGENT_NAME = "github_agent"


class GithubAgent:
    name = AGENT_NAME

    async def list_repos(self, per_page: int = 15) -> list[dict]:
        if not settings.github_token.get_secret_value():
            raise RuntimeError("GITHUB_TOKEN is not configured")
        return await gh.list_repos(per_page=per_page)

    async def run(self, state: AgentState) -> AgentState:
        """Produce a WhatsApp-friendly GitHub repo list (single-shot helper)."""
        try:
            repos = await self.list_repos()
        except Exception as e:
            logger.exception("%s list failed", AGENT_NAME)
            return tag(
                {
                    **state,
                    "status": "failed",
                    "notification_text": f"GitHub agent error: {e}",
                },
                AGENT_NAME,
            )
        if not repos:
            return tag(
                {
                    **state,
                    "status": "repo_picker",
                    "notification_text": (
                        f"No GitHub repos found for `{settings.github_default_owner or 'token user'}`."
                    ),
                },
                AGENT_NAME,
            )
        lines = [
            f"{i}. *{r.get('name')}* — {r.get('html_url', '')}"
            for i, r in enumerate(repos, 1)
        ]
        return tag(
            {
                **state,
                "status": "repo_picker",
                "repo_provider": "github",
                "session_data": {**(state.get("session_data") or {}), "repos": repos},
                "notification_text": "*GitHub repositories*\n\n"
                + "\n".join(lines)
                + "\n\nReply with a number.",
            },
            AGENT_NAME,
        )


github_agent = GithubAgent()
