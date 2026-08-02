"""Push branch and open PR before final deploy approval (multi-gate workflow)."""

from __future__ import annotations

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services import azure_devops as azdo
from agent.services.git_ops import commit_and_push, open_workspace
from agent.services.github import create_pull_request, parse_repo_url

logger = get_logger(__name__)


def _detect_provider(state: AgentState) -> str:
    provider = (state.get("repo_provider") or "").lower()
    if provider in ("github", "azure_devops"):
        return provider
    url = state.get("repo_url", "")
    if "dev.azure.com" in url or "visualstudio.com" in url:
        return "azure_devops"
    return "github"


async def publish_pull_request(state: AgentState) -> AgentState:
    """Commit, push feature branch, and create PR — do not merge yet."""
    task_id = state.get("task_id", "unknown")
    workspace_path = state.get("workspace_path", "")
    branch_name = state.get("branch_name", f"agent/{task_id}")
    user_msg = state.get("user_message", "") or state.get("email_body", "")
    provider = _detect_provider(state)
    owner = state.get("repo_owner", "")
    repo_name = state.get("repo_name", "")

    if not workspace_path:
        return {
            **state,
            "status": "failed",
            "error": "Missing workspace",
            "notification_text": "Cannot open PR — workspace missing.",
        }

    if provider == "github" and (not owner or not repo_name):
        try:
            owner, repo_name = parse_repo_url(state.get("repo_url", ""))
        except Exception:
            return {
                **state,
                "status": "failed",
                "notification_text": "Cannot open PR — could not parse GitHub repo.",
            }

    try:
        repo = open_workspace(workspace_path)
        commit_msg = f"agent({task_id}): {user_msg[:80]}"
        sha = commit_and_push(repo, commit_msg, branch_name)
        if not sha:
            return {
                **state,
                "status": "failed",
                "notification_text": "No changes to push for PR.",
            }

        changes = state.get("file_changes") or []
        body_lines = [
            "## Automated by Sunny's Personal AI Agent",
            f"**Task ID:** {task_id}",
            f"**Request:** {user_msg[:300]}",
            "",
            "### Changes",
            *[f"- {c.get('action', 'modify')} `{c['path']}`" for c in changes],
        ]
        if state.get("implementation_plan"):
            body_lines.extend(["", "### Implementation plan", "Approved via WhatsApp before development."])

        pr_title = f"[AI Agent] {user_msg[:60]}"
        pr_body = "\n".join(body_lines)

        if provider == "azure_devops":
            return await _publish_azdo_pr(state, task_id, sha, branch_name, pr_title, pr_body)

        pr = await create_pull_request(
            owner=owner,
            repo=repo_name,
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base="main",
        )
        pr_url = pr.get("html_url", "")
        pr_number = pr.get("number")
        logger.info("Opened GitHub PR #%s for task %s", pr_number, task_id)
        return {
            **state,
            "status": "pr_created",
            "commit_sha": sha,
            "pr_url": pr_url,
            "pr_number": pr_number,
            "notification_text": f"PR opened: {pr_url}",
        }
    except Exception as e:
        logger.exception("PR publish failed for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "error": str(e),
            "notification_text": f"Failed to open PR: {e}",
        }


async def _publish_azdo_pr(
    state: AgentState,
    task_id: str,
    sha: str,
    branch_name: str,
    title: str,
    body: str,
) -> AgentState:
    from agent.config import settings

    project = state.get("azdo_project", "")
    repo_id = state.get("azdo_repo_id", "")
    repo_name = state.get("repo_name", "")

    if not project or not repo_id:
        url = state.get("repo_url", "")
        try:
            parts = url.split("/")
            if "_git" in parts:
                gi = parts.index("_git")
                project = project or parts[gi - 1]
                repo_name = repo_name or parts[gi + 1]
                repos = await azdo.list_repositories(project)
                match = next((r for r in repos if r.get("name") == repo_name), None)
                if match:
                    repo_id = match["id"]
        except Exception:
            logger.exception("Failed resolving AzDO repo for PR")

    if not project or not repo_id:
        return {
            **state,
            "status": "failed",
            "commit_sha": sha,
            "notification_text": "Pushed branch but could not open AzDO PR (missing project/repo id).",
        }

    pr = await azdo.create_pull_request(
        project=project,
        repo_id=repo_id,
        title=title,
        description=body,
        source_branch=branch_name,
    )
    pr_id = pr.get("pullRequestId")
    org = settings.azdo_org_url.rstrip("/")
    pr_url = f"{org}/{project}/_git/{repo_name or repo_id}/pullrequest/{pr_id}"
    logger.info("Opened AzDO PR #%s for task %s", pr_id, task_id)

    # Watch PR-branch CI so test failures can offer the test_fixer agent early.
    phone = state.get("whatsapp_phone") or ""
    pipeline_url = ""
    try:
        from agent.services.ci_watch import save_ci_watch, start_azdo_ci_watch

        pipeline = await azdo.find_pipeline_for_repo(project, repo_name or "")
        if pipeline:
            pipeline_url = f"{org}/{project}/_build?definitionId={pipeline.get('id')}"
        if phone:
            await save_ci_watch(
                task_id=task_id,
                phone=phone,
                project=project,
                repo_name=repo_name or "",
                repo_id=str(repo_id),
                pr_url=pr_url,
                pipeline_url=pipeline_url,
                commit_sha=sha,
                live_url=f"{settings.agent_app_url.rstrip('/')}/portal",
                notes="pr_validation",
            )
            start_azdo_ci_watch(task_id)
    except Exception:
        logger.exception("Failed starting PR CI watch for task %s", task_id)

    return {
        **state,
        "status": "pr_created",
        "commit_sha": sha,
        "pr_url": pr_url,
        "pr_id": pr_id,
        "azdo_project": project,
        "azdo_repo_id": str(repo_id),
        "repo_name": repo_name,
        "pipeline_url": pipeline_url,
        "pipeline_status": "watching" if pipeline_url else state.get("pipeline_status"),
        "notification_text": f"PR opened: {pr_url}",
    }
