from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.services.git_ops import commit_and_push
from agent.services.github import (
    create_pull_request,
    list_workflows,
    merge_pull_request,
    parse_repo_url,
    trigger_workflow,
)
from agent.services import azure_devops as azdo
from agent.services.sample_deploy import (
    deploy_sample_app_from_github,
    deploy_sample_app_from_workspace,
)

logger = get_logger(__name__)


def _detect_provider(state: AgentState) -> str:
    provider = (state.get("repo_provider") or "").lower()
    if provider in ("github", "azure_devops"):
        return provider
    url = state.get("repo_url", "")
    if "dev.azure.com" in url or "visualstudio.com" in url:
        return "azure_devops"
    return "github"


async def _merge_and_live_deploy(
    *,
    owner: str,
    repo_name: str,
    pr_number: int,
    task_id: str,
    user_msg: str,
    workspace_path: str,
) -> tuple[bool, str, str]:
    """Merge PR to main, then live-deploy sample app. Returns (merged, live_url, error)."""
    merged = False
    live_url = ""
    error = ""

    try:
        await merge_pull_request(
            owner,
            repo_name,
            int(pr_number),
            commit_title=f"agent({task_id}): {user_msg[:60]}",
        )
        merged = True
        logger.info("Merged PR #%s for task %s", pr_number, task_id)
    except Exception as e:
        msg = str(e).lower()
        if "already merged" in msg or "405" in msg or "pull request is not mergeable" in msg:
            # Treat already-merged as success for deploy path
            logger.warning("Merge reported issue for PR #%s: %s — continuing to deploy", pr_number, e)
            merged = True
        else:
            logger.exception("Merge failed for task %s", task_id)
            return False, "", f"Merge failed: {e}"

    # Prefer workspace zip (exact approved files); fall back to GitHub main zipball
    try:
        live_url = await deploy_sample_app_from_workspace(workspace_path)
    except Exception as e1:
        logger.warning("Workspace live-deploy failed (%s); trying GitHub main", e1)
        try:
            live_url = await deploy_sample_app_from_github(owner, repo_name, ref="main")
        except Exception as e2:
            logger.exception("GitHub-main live-deploy also failed for task %s", task_id)
            error = f"Live deploy failed: {e2}"

    return merged, live_url, error


async def deploy_code(state: AgentState) -> AgentState:
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
        from agent.services.git_ops import open_workspace

        repo = open_workspace(workspace_path)
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
        pr_number = pr.get("number")

        pipeline_url = ""
        try:
            workflows = await list_workflows(owner, repo_name)
            deploy_wf = next(
                (
                    w
                    for w in workflows
                    if "deploy" in (w.get("name") or "").lower()
                    or "deploy" in (w.get("path") or "").lower()
                ),
                None,
            )
            wf = deploy_wf or (workflows[0] if workflows else None)
            if wf:
                triggered = await trigger_workflow(
                    owner, repo_name, str(wf["id"]), "main" if deploy_wf else branch_name
                )
                if triggered:
                    pipeline_url = f"https://github.com/{owner}/{repo_name}/actions"
        except Exception:
            logger.warning("Could not trigger CI workflow for task %s", task_id)

        merged = False
        live_url = ""
        deploy_error = ""
        if repo_name == settings.sample_app_github_repo and pr_number:
            merged, live_url, deploy_error = await _merge_and_live_deploy(
                owner=owner,
                repo_name=repo_name,
                pr_number=int(pr_number),
                task_id=task_id,
                user_msg=user_msg,
                workspace_path=workspace_path,
            )
            if live_url:
                pipeline_url = pipeline_url or f"https://github.com/{owner}/{repo_name}/actions"

        lines = [
            "*Final deployment complete*" if live_url else "*Deployment result*",
            f"*Merged to main:* {'yes' if merged else 'no'}",
        ]
        if live_url:
            lines.extend(
                [
                    f"*Live app:* {live_url}",
                    f"*Docs:* {live_url}/docs",
                    "Open on your *phone browser* and refresh — no GitHub visit needed.",
                ]
            )
        else:
            lines.append(f"*PR:* {pr_url}")
            if deploy_error:
                lines.append(f"*Live deploy error:* {deploy_error}")
            else:
                lines.append("Live deploy was skipped for this repo.")

        detail = "\n".join(lines)
        logger.info(
            "Deployed task %s: PR=%s merged=%s live=%s",
            task_id,
            pr_url,
            merged,
            live_url or "-",
        )
        return {
            **state,
            "status": "completed" if (merged or pr_url) else "failed",
            "commit_sha": sha,
            "pr_url": pr_url,
            "pipeline_url": pipeline_url or "N/A",
            "repo_provider": "github",
            "notification_text": detail,
            "evaluation_text": (
                f"1. Your WhatsApp approval authorized deployment\n"
                f"2. Changes merged to `main`: {'yes' if merged else 'no'}\n"
                f"3. Live deploy: {live_url or deploy_error or 'skipped'}\n"
                f"4. Pipeline: {pipeline_url or 'N/A'}\n"
                + (
                    "5. Done — refresh phone browser to see changes"
                    if live_url
                    else "5. Code is on GitHub; live app still needs a successful deploy"
                )
            ),
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
    from agent.services.sample_deploy import deploy_agent_app_from_workspace

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
            logger.exception("Failed to resolve AzDO project/repo from URL")

    # Recover ids from HTTP workspace meta if still missing
    if (not project or not repo_id) and state.get("workspace_path"):
        try:
            from agent.services.git_ops import open_workspace

            ws = open_workspace(state["workspace_path"])
            if getattr(ws, "azdo_project", ""):
                project = project or ws.azdo_project
                repo_id = repo_id or ws.azdo_repo_id
                repo_name = repo_name or ws.repo_name
        except Exception:
            logger.exception("Failed to read AzDO workspace meta")

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
    pr_url = f"{org}/{project}/_git/{repo_name or repo_id}/pullrequest/{pr_id}"

    pipeline_url = "N/A"
    try:
        pipelines = await azdo.list_pipelines(project)
        if pipelines:
            run = await azdo.trigger_pipeline(project, pipelines[0]["id"], "main")
            run_id = run.get("id")
            pipeline_url = (
                f"{org}/{project}/_build?definitionId={pipelines[0]['id']}&id={run_id}"
            )
    except Exception:
        logger.warning("Could not trigger AzDO pipeline for task %s", task_id)

    merged = False
    live_url = ""
    deploy_error = ""
    is_demo_repo = (repo_name or "").lower() == settings.azdo_demo_repo.lower() or (
        project.lower() == settings.azdo_demo_project.lower()
        and (repo_name or "").lower() == settings.azdo_demo_repo.lower()
    )

    if is_demo_repo and pr_id:
        try:
            await azdo.merge_pull_request(project, str(repo_id), int(pr_id))
            merged = True
        except Exception as e:
            msg = str(e).lower()
            if "completed" in msg or "already" in msg:
                merged = True
            else:
                logger.exception("AzDO merge failed for task %s", task_id)
                deploy_error = f"Merge failed: {e}"

        if merged:
            try:
                live_url = await deploy_agent_app_from_workspace(
                    state.get("workspace_path", "")
                )
                live_url = f"{live_url.rstrip('/')}/portal"
            except Exception as e:
                logger.exception("AzDO live deploy failed for task %s", task_id)
                deploy_error = f"Live deploy failed: {e}"

    if live_url:
        detail = (
            "*Final deployment complete*\n"
            f"*Merged to main:* yes\n"
            f"*Live portal:* {live_url}\n"
            "Open on your *phone browser* and refresh — no Azure DevOps visit needed."
        )
    else:
        detail = (
            f"*Azure DevOps PR:* {pr_url}\n"
            f"*Merged to main:* {'yes' if merged else 'no'}\n"
        )
        if deploy_error:
            detail += f"*Live deploy error:* {deploy_error}"
        else:
            detail += "Live deploy runs automatically for web.Whatsapp-AI-Agent after approve."

    return {
        **state,
        "status": "completed" if (merged or pr_url) else "failed",
        "commit_sha": sha,
        "pr_url": pr_url,
        "pipeline_url": pipeline_url,
        "repo_provider": "azure_devops",
        "azdo_project": project,
        "azdo_repo_id": str(repo_id),
        "repo_name": repo_name,
        "notification_text": detail,
        "evaluation_text": (
            "1. Your WhatsApp approval authorized Azure DevOps deployment\n"
            f"2. Changes merged to `main`: {'yes' if merged else 'no'}\n"
            f"3. Live portal: {live_url or deploy_error or 'skipped'}\n"
            f"4. Pipeline: {pipeline_url}\n"
            + (
                "5. Done — refresh phone browser on /portal"
                if live_url
                else "5. PR created; live portal still needs a successful deploy"
            )
        ),
    }
