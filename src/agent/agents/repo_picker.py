from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.core.session import clear_session, get_session, save_session
from agent.services import azure_devops as azdo
from agent.services import github as gh

logger = get_logger(__name__)


async def browse_repos(state: AgentState) -> AgentState:
    """Multi-turn wizard: provider → (AzDO project) → repo → optional code instruction."""
    phone = state.get("whatsapp_phone", "")
    user_msg = (state.get("user_message") or "").strip()
    session = await get_session(phone) if phone else {"awaiting": None, "provider": None, "data": {}}
    awaiting = state.get("session_awaiting") or session.get("awaiting")
    provider = (state.get("repo_provider") or session.get("provider") or "").lower()
    data = dict(session.get("data") or {})

    # Kickoff
    if not awaiting:
        await save_session(phone, awaiting="provider", data={})
        return {
            **state,
            "status": "repo_picker",
            "intent": "browse_repos",
            "notification_text": (
                "*Choose repository provider*\n\n"
                "1. GitHub\n"
                "2. Azure DevOps\n\n"
                "Reply with *1* / *2* or the name."
            ),
        }

    if awaiting == "provider":
        choice = user_msg.lower()
        if choice in ("1", "github", "gh"):
            provider = "github"
        elif choice in ("2", "azure", "azdo", "azure devops", "ado"):
            provider = "azure_devops"
        else:
            return {
                **state,
                "status": "repo_picker",
                "notification_text": "Please reply *1* (GitHub) or *2* (Azure DevOps).",
            }

        await save_session(phone, awaiting="repo" if provider == "github" else "project", provider=provider, data={})
        if provider == "github":
            return await _list_github_repos(state, phone)
        return await _list_azdo_projects(state, phone)

    if awaiting == "project" and provider == "azure_devops":
        projects = data.get("projects") or []
        selected = _pick_from_list(user_msg, projects, key="name")
        if not selected:
            return {
                **state,
                "status": "repo_picker",
                "notification_text": "Please reply with the project number or exact name.",
            }
        data["azdo_project"] = selected["name"]
        await save_session(phone, awaiting="repo", provider=provider, data=data)
        return await _list_azdo_repos(state, phone, selected["name"], data)

    if awaiting == "repo":
        repos = data.get("repos") or []
        selected = _pick_from_list(user_msg, repos, key="name")
        if not selected:
            return {
                **state,
                "status": "repo_picker",
                "notification_text": "Please reply with the repo number or exact name.",
            }

        if provider == "github":
            repo_url = selected.get("html_url") or selected.get("clone_url") or ""
            data.update({"repo_url": repo_url, "repo_name": selected.get("name")})
        else:
            project = data.get("azdo_project", "")
            remote = selected.get("remoteUrl") or ""
            data.update(
                {
                    "repo_url": remote,
                    "repo_name": selected.get("name"),
                    "azdo_project": project,
                    "azdo_repo_id": selected.get("id"),
                }
            )

        await save_session(phone, awaiting="code_instruction", provider=provider, data=data)
        return {
            **state,
            "status": "repo_picker",
            "repo_url": data.get("repo_url", ""),
            "repo_provider": provider,
            "azdo_project": data.get("azdo_project", ""),
            "azdo_repo_id": data.get("azdo_repo_id", ""),
            "notification_text": (
                f"Selected: *{selected.get('name')}*\n"
                f"{data.get('repo_url')}\n\n"
                "What should I do in this repo?\n"
                "Example: `add DELETE /tasks/{{id}} endpoint` or `fix validation on title`"
            ),
        }

    if awaiting == "code_instruction":
        instruction = user_msg
        repo_url = data.get("repo_url") or state.get("repo_url", "")
        if not repo_url:
            await clear_session(phone)
            return {
                **state,
                "status": "failed",
                "notification_text": "Session lost the repo URL. Say *check my repos* to start again.",
            }
        # Hand off to develop_code path
        await clear_session(phone)
        return {
            **state,
            "intent": "code_change",
            "user_message": instruction,
            "repo_url": repo_url,
            "repo_provider": provider or data.get("provider") or "github",
            "azdo_project": data.get("azdo_project", ""),
            "azdo_repo_id": data.get("azdo_repo_id", ""),
            "status": "task_started",
            "notification_text": (
                f"Starting development on `{data.get('repo_name')}`\n"
                f"*Request:* {instruction}"
            ),
        }

    await clear_session(phone)
    return {
        **state,
        "status": "repo_picker",
        "notification_text": "Session reset. Say *check my repos* to browse again.",
    }


def _pick_from_list(user_msg: str, items: list[dict], key: str = "name") -> dict | None:
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


async def _list_github_repos(state: AgentState, phone: str) -> AgentState:
    try:
        repos = await gh.list_repos(per_page=15)
    except Exception as e:
        logger.exception("GitHub list repos failed")
        await clear_session(phone)
        return {
            **state,
            "status": "failed",
            "notification_text": f"Could not list GitHub repos: {e}",
        }
    if not repos:
        await clear_session(phone)
        owner = settings.github_default_owner or "your account"
        return {
            **state,
            "status": "repo_picker",
            "notification_text": (
                f"No repos found for {owner}. Check GITHUB_TOKEN / GITHUB_DEFAULT_OWNER."
            ),
        }
    lines = [f"{i}. *{r.get('name')}* — {r.get('html_url', '')}" for i, r in enumerate(repos, 1)]
    await save_session(
        phone,
        awaiting="repo",
        provider="github",
        data={"repos": repos},
    )
    return {
        **state,
        "status": "repo_picker",
        "repo_provider": "github",
        "notification_text": "*GitHub repositories*\n\n" + "\n".join(lines) + "\n\nReply with a number.",
    }


async def _list_azdo_projects(state: AgentState, phone: str) -> AgentState:
    try:
        projects = await azdo.list_projects()
    except Exception as e:
        logger.exception("AzDO list projects failed")
        await clear_session(phone)
        return {
            **state,
            "status": "failed",
            "notification_text": f"Could not list Azure DevOps projects: {e}",
        }
    slim = [{"id": p.get("id"), "name": p.get("name")} for p in projects]
    lines = [f"{i}. *{p['name']}*" for i, p in enumerate(slim, 1)]
    await save_session(
        phone,
        awaiting="project",
        provider="azure_devops",
        data={"projects": slim},
    )
    return {
        **state,
        "status": "repo_picker",
        "repo_provider": "azure_devops",
        "notification_text": "*Azure DevOps projects*\n\n" + "\n".join(lines) + "\n\nReply with a number.",
    }


async def _list_azdo_repos(state: AgentState, phone: str, project: str, data: dict) -> AgentState:
    try:
        repos = await azdo.list_repositories(project)
    except Exception as e:
        logger.exception("AzDO list repos failed")
        return {
            **state,
            "status": "failed",
            "notification_text": f"Could not list repos in {project}: {e}",
        }
    slim = [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "remoteUrl": r.get("remoteUrl") or r.get("webUrl"),
        }
        for r in repos
    ]
    lines = [f"{i}. *{r['name']}*" for i, r in enumerate(slim, 1)]
    data = {**data, "repos": slim, "azdo_project": project}
    await save_session(phone, awaiting="repo", provider="azure_devops", data=data)
    return {
        **state,
        "status": "repo_picker",
        "repo_provider": "azure_devops",
        "azdo_project": project,
        "notification_text": (
            f"*Repos in {project}*\n\n" + "\n".join(lines) + "\n\nReply with a number."
        ),
    }
