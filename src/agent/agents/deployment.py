from pathlib import Path

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.services.git_ops import commit_and_push
from agent.services.github import create_pull_request, list_workflows, parse_repo_url, trigger_workflow
from agent.services import azure_devops as azdo

logger = get_logger(__name__)


def _detect_provider(state: AgentState) -> str:
    provider = (state.get("repo_provider") or "").lower()
    if provider in ("github", "azure_devops"):
        return provider
    url = state.get("repo_url", "")
    if "dev.azure.com" in url or "visualstudio.com" in url:
        return "azure_devops"
    return "github"


async def deploy_code(state: AgentState) -> AgentState:
    from git import Repo

    task_id = state.get("task_id", "unknown")
    workspace_path = state.get("workspace_path", "")
    branch_name = state.get("branch_name", "")
    owner = state.get("repo_owner", "")
    repo_name = state.get("repo_name", "")
    user_msg = state.get("user_message", "") or state.get("email_body", "")
    provider = _detect_provider(state)

    if not all([workspace_path, branch_name]):
        return {
            **state,
            "status": "failed",
            "error": "Missing deployment context",
            "notification_text": "Deployment failed — missing repo or branch info.",
        }

    if provider == "github" and not all([owner, repo_name]):
        try:
            owner, repo_name = parse_repo_url(state.get("repo_url", ""))
        except Exception:
            return {
                **state,
                "status": "failed",
                "notification_text": "Deployment failed — could not parse GitHub owner/repo.",
            }

    try:
        repo = Repo(workspace_path)
        commit_msg = f"agent({task_id}): {user_msg[:80]}"
        sha = commit_and_push(repo, commit_msg, branch_name)

        if not sha:
            return {
                **state,
                "status": "failed",
                "error": "No changes to push",
                "notification_text": "No changes to push.",
            }

        if provider == "azure_devops":
            return await _deploy_azdo(state, task_id, sha, branch_name, user_msg)

        pr = await create_pull_request(
            owner=owner,
            repo=repo_name,
            title=f"[AI Agent] {user_msg[:60]}",
            body=(
                f"## Automated by Sunny's Personal AI Agent\n\n"
                f"**Task ID:** {task_id}\n"
                f"**Request:** {user_msg[:200]}\n\n"
                f"### Changes\n"
                + "\n".join(
                    f"- {c.get('action', 'modify')} `{c['path']}`"
                    for c in state.get("file_changes", [])
                )
            ),
            head=branch_name,
            base="main",
        )
        pr_url = pr.get("html_url", "")

        pipeline_url = ""
        try:
            workflows = await list_workflows(owner, repo_name)
            if workflows:
                triggered = await trigger_workflow(
                    owner, repo_name, str(workflows[0]["id"]), branch_name
                )
                if triggered:
                    pipeline_url = f"https://github.com/{owner}/{repo_name}/actions"
        except Exception:
            logger.warning("Could not trigger CI workflow for task %s", task_id)

        logger.info("Deployed task %s: PR=%s", task_id, pr_url)
        return {
            **state,
            "status": "completed",
            "commit_sha": sha,
            "pr_url": pr_url,
            "pipeline_url": pipeline_url or "N/A",
            "repo_provider": "github",
            "notification_text": f"PR created: {pr_url}",
        }

    except Exception as e:
        logger.exception("Deployment failed for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "error": str(e),
            "notification_text": f"Deployment failed: {e}",
        }


async def _deploy_azdo(
    state: AgentState,
    task_id: str,
    sha: str,
    branch_name: str,
    user_msg: str,
) -> AgentState:
    project = state.get("azdo_project", "")
    repo_id = state.get("azdo_repo_id", "")
    if not project or not repo_id:
        # Best-effort parse from URL: https://dev.azure.com/{org}/{project}/_git/{repo}
        url = state.get("repo_url", "")
        try:
            parts = url.split("/")
            # .../org/project/_git/repo
            if "_git" in parts:
                gi = parts.index("_git")
                project = project or parts[gi - 1]
                repo_name = parts[gi + 1]
                repos = await azdo.list_repositories(project)
                match = next((r for r in repos if r.get("name") == repo_name), None)
                if match:
                    repo_id = match["id"]
        except Exception:
            logger.exception("Failed to resolve AzDO project/repo from URL")

    if not project or not repo_id:
        return {
            **state,
            "status": "failed",
            "commit_sha": sha,
            "notification_text": (
                "Pushed branch but could not create AzDO PR "
                "(missing project/repo id). Branch is on the remote."
            ),
        }

    pr = await azdo.create_pull_request(
        project=project,
        repo_id=repo_id,
        title=f"[AI Agent] {user_msg[:60]}",
        description=f"Task {task_id}: {user_msg[:300]}",
        source_branch=branch_name,
    )
    pr_id = pr.get("pullRequestId")
    org = settings.azdo_org_url.rstrip("/")
    pr_url = f"{org}/{project}/_git/{state.get('repo_name', repo_id)}/pullrequest/{pr_id}"

    pipeline_url = "N/A"
    try:
        pipelines = await azdo.list_pipelines(project)
        if pipelines:
            run = await azdo.trigger_pipeline(project, pipelines[0]["id"], branch_name)
            run_id = run.get("id")
            pipeline_url = f"{org}/{project}/_build?definitionId={pipelines[0]['id']}&id={run_id}"
    except Exception:
        logger.warning("Could not trigger AzDO pipeline for task %s", task_id)

    return {
        **state,
        "status": "completed",
        "commit_sha": sha,
        "pr_url": pr_url,
        "pipeline_url": pipeline_url,
        "repo_provider": "azure_devops",
        "azdo_project": project,
        "azdo_repo_id": str(repo_id),
        "notification_text": f"Azure DevOps PR created: {pr_url}",
    }
