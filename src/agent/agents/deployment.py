from pathlib import Path

from git import Repo

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.git_ops import commit_and_push
from agent.services.github import create_pull_request, trigger_workflow, list_workflows

logger = get_logger(__name__)


async def deploy_code(state: AgentState) -> AgentState:
    task_id = state.get("task_id", "unknown")
    workspace_path = state.get("workspace_path", "")
    branch_name = state.get("branch_name", "")
    owner = state.get("repo_owner", "")
    repo_name = state.get("repo_name", "")
    user_msg = state.get("user_message", "") or state.get("email_body", "")

    if not all([workspace_path, branch_name, owner, repo_name]):
        return {
            **state,
            "status": "failed",
            "error": "Missing deployment context",
            "notification_text": "Deployment failed — missing repo or branch info.",
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

        pr = await create_pull_request(
            owner=owner,
            repo=repo_name,
            title=f"[AI Agent] {user_msg[:60]}",
            body=(
                f"## Automated by AI Agent\n\n"
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
                    owner, repo_name, workflows[0]["id"], branch_name
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
