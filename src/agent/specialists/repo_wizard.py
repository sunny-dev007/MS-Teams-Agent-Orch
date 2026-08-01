"""Repo wizard specialist — multi-turn provider→project→repo→instruction.

Delegates list operations to GithubAgent / AzdoAgent (never mixes providers in one call).
"""

from __future__ import annotations

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.core.session import clear_session, get_session, save_session
from agent.specialists.azdo_agent import azdo_agent
from agent.specialists.base import tag
from agent.specialists.github_agent import github_agent

logger = get_logger(__name__)
AGENT_NAME = "repo_wizard"


class RepoWizardAgent:
    name = AGENT_NAME

    async def run(self, state: AgentState) -> AgentState:
        phone = state.get("whatsapp_phone", "")
        user_msg = (state.get("user_message") or "").strip()
        session = await get_session(phone) if phone else {"awaiting": None, "provider": None, "data": {}}
        awaiting = state.get("session_awaiting") or session.get("awaiting")
        provider = (state.get("repo_provider") or session.get("provider") or "").lower()
        data = dict(session.get("data") or {})

        if not awaiting:
            await save_session(phone, awaiting="provider", data={})
            return tag(
                {
                    **state,
                    "status": "repo_picker",
                    "intent": "browse_repos",
                    "notification_text": (
                        "*Choose repository provider*\n\n"
                        "1. GitHub  *(github_agent)*\n"
                        "2. Azure DevOps  *(azdo_agent)*\n\n"
                        "Reply with *1* / *2* or the name."
                    ),
                },
                AGENT_NAME,
            )

        if awaiting == "provider":
            choice = user_msg.lower()
            if choice in ("1", "github", "gh"):
                provider = "github"
            elif choice in ("2", "azure", "azdo", "azure devops", "ado"):
                provider = "azure_devops"
            else:
                return tag(
                    {
                        **state,
                        "status": "repo_picker",
                        "notification_text": "Please reply *1* (GitHub) or *2* (Azure DevOps).",
                    },
                    AGENT_NAME,
                )

            if provider == "github":
                listed = await github_agent.run({**state, "repo_provider": "github"})
                repos = (listed.get("session_data") or {}).get("repos") or []
                await save_session(phone, awaiting="repo", provider="github", data={"repos": repos})
                return tag(listed, AGENT_NAME)

            listed = await azdo_agent.run({**state, "repo_provider": "azure_devops"})
            projects = (listed.get("session_data") or {}).get("projects") or []
            await save_session(
                phone, awaiting="project", provider="azure_devops", data={"projects": projects}
            )
            return tag(listed, AGENT_NAME)

        if awaiting == "project" and provider == "azure_devops":
            projects = data.get("projects") or []
            selected = _pick(user_msg, projects, key="name")
            if not selected:
                return tag(
                    {
                        **state,
                        "status": "repo_picker",
                        "notification_text": "Please reply with the project number or exact name.",
                    },
                    AGENT_NAME,
                )
            try:
                repos_raw = await azdo_agent.list_repositories(selected["name"])
            except Exception as e:
                logger.exception("%s azdo repos failed", AGENT_NAME)
                return tag(
                    {
                        **state,
                        "status": "failed",
                        "notification_text": f"Azure DevOps repos error: {e}",
                    },
                    AGENT_NAME,
                )
            slim = [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "remoteUrl": r.get("remoteUrl") or r.get("webUrl"),
                }
                for r in repos_raw
            ]
            data.update({"azdo_project": selected["name"], "repos": slim})
            await save_session(phone, awaiting="repo", provider="azure_devops", data=data)
            lines = [f"{i}. *{r['name']}*" for i, r in enumerate(slim, 1)]
            return tag(
                {
                    **state,
                    "status": "repo_picker",
                    "repo_provider": "azure_devops",
                    "azdo_project": selected["name"],
                    "notification_text": (
                        f"*Repos in {selected['name']}* _(azdo_agent)_\n\n"
                        + "\n".join(lines)
                        + "\n\nReply with a number."
                    ),
                },
                AGENT_NAME,
            )

        if awaiting == "repo":
            repos = data.get("repos") or []
            selected = _pick(user_msg, repos, key="name")
            if not selected:
                return tag(
                    {
                        **state,
                        "status": "repo_picker",
                        "notification_text": "Please reply with the repo number or exact name.",
                    },
                    AGENT_NAME,
                )

            if provider == "github":
                repo_url = selected.get("html_url") or selected.get("clone_url") or ""
                data.update({"repo_url": repo_url, "repo_name": selected.get("name")})
            else:
                data.update(
                    {
                        "repo_url": selected.get("remoteUrl") or "",
                        "repo_name": selected.get("name"),
                        "azdo_project": data.get("azdo_project", ""),
                        "azdo_repo_id": selected.get("id"),
                    }
                )

            await save_session(phone, awaiting="code_instruction", provider=provider, data=data)
            return tag(
                {
                    **state,
                    "status": "repo_picker",
                    "repo_url": data.get("repo_url", ""),
                    "repo_provider": provider,
                    "azdo_project": data.get("azdo_project", ""),
                    "azdo_repo_id": data.get("azdo_repo_id", ""),
                    "notification_text": (
                        f"Selected via *{provider}*: `{selected.get('name')}`\n"
                        f"{data.get('repo_url')}\n\n"
                        "What should I do in this repo?\n"
                        "Example: `add DELETE /tasks/{{id}}` or `fix empty title validation`"
                    ),
                },
                AGENT_NAME,
            )

        if awaiting == "code_instruction":
            instruction = user_msg
            repo_url = data.get("repo_url") or state.get("repo_url", "")
            if not repo_url:
                await clear_session(phone)
                return tag(
                    {
                        **state,
                        "status": "failed",
                        "notification_text": "Session lost the repo URL. Say *check my repos* again.",
                    },
                    AGENT_NAME,
                )
            await clear_session(phone)
            return tag(
                {
                    **state,
                    "intent": "code_change",
                    "user_message": instruction,
                    "repo_url": repo_url,
                    "repo_provider": provider or "github",
                    "azdo_project": data.get("azdo_project", ""),
                    "azdo_repo_id": data.get("azdo_repo_id", ""),
                    "status": "task_started",
                    "notification_text": (
                        f"Handing off to *coding_developer*\n"
                        f"Repo: `{data.get('repo_name')}`\n"
                        f"Request: {instruction}"
                    ),
                },
                AGENT_NAME,
            )

        await clear_session(phone)
        return tag(
            {
                **state,
                "status": "repo_picker",
                "notification_text": "Session reset. Say *check my repos* to browse again.",
            },
            AGENT_NAME,
        )


def _pick(user_msg: str, items: list[dict], key: str = "name") -> dict | None:
    if not items:
        return None
    msg = user_msg.strip()
    if msg.isdigit():
        idx = int(msg) - 1
        if 0 <= idx < len(items):
            return items[idx]
    msg_l = msg.lower()
    for item in items:
        name = str(item.get(key, "")).lower()
        if name == msg_l or msg_l in name:
            return item
    return None


repo_wizard = RepoWizardAgent()


async def run_repo_wizard(state: AgentState) -> AgentState:
    return await repo_wizard.run(state)
