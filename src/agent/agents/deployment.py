import asyncio

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.services.ci_gate import check_ci_busy_for_context, wait_for_ci_idle
from agent.services.git_ops import commit_and_push
from agent.services.github import (
    create_pull_request,
    merge_pull_request,
    parse_repo_url,
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


async def _merge_github_pr(
    *,
    owner: str,
    repo_name: str,
    pr_number: int,
    task_id: str,
    user_msg: str,
) -> tuple[bool, str]:
    """Merge GitHub PR only (CI owns first deploy). Returns (merged, error)."""
    try:
        await merge_pull_request(
            owner,
            repo_name,
            int(pr_number),
            commit_title=f"agent({task_id}): {user_msg[:60]}",
        )
        logger.info("Merged PR #%s for task %s", pr_number, task_id)
        return True, ""
    except Exception as e:
        msg = str(e).lower()
        if "already merged" in msg or "405" in msg or "pull request is not mergeable" in msg:
            logger.warning("Merge reported issue for PR #%s: %s — continuing", pr_number, e)
            return True, ""
        logger.exception("Merge failed for task %s", task_id)
        return False, f"Merge failed: {e}"


async def _kudu_sample_fallback(workspace_path: str, owner: str, repo_name: str) -> tuple[str, str]:
    try:
        live_url = await deploy_sample_app_from_workspace(workspace_path)
        return live_url, ""
    except Exception as e1:
        logger.warning("Workspace live-deploy failed (%s); trying GitHub main", e1)
        try:
            live_url = await deploy_sample_app_from_github(owner, repo_name, ref="main")
            return live_url, ""
        except Exception as e2:
            logger.exception("GitHub-main live-deploy also failed")
            return "", f"Live deploy failed: {e2}"


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

    # Re-check provider CI before mutating remotes (isolated per provider/repo)
    ci_ctx = {
        "provider": provider,
        "owner": owner,
        "repo_name": repo_name,
        "repo_url": state.get("repo_url", ""),
        "project": state.get("azdo_project", ""),
        "repo_id": state.get("azdo_repo_id", ""),
    }
    busy = await check_ci_busy_for_context(ci_ctx)
    if busy.busy:
        phone = state.get("whatsapp_phone", "")
        if phone and task_id:
            try:
                from agent.core.session import save_session

                await save_session(
                    phone,
                    awaiting="approval",
                    provider=provider,
                    data={
                        "pending_task_id": task_id,
                        "workspace_path": workspace_path,
                        "branch_name": branch_name,
                        "repo_url": state.get("repo_url", ""),
                        "repo_owner": owner,
                        "repo_name": repo_name,
                        "repo_provider": provider,
                        "azdo_project": state.get("azdo_project", ""),
                        "azdo_repo_id": state.get("azdo_repo_id", ""),
                        "user_message": user_msg,
                        "file_changes": state.get("file_changes") or [],
                    },
                    merge_data=False,
                )
            except Exception:
                logger.exception("Failed to re-persist approval session after CI busy")
        return {
            **state,
            "status": "awaiting_approval",
            "approval_status": "pending",
            "pipeline_status": "busy",
            "pipeline_url": busy.url or state.get("pipeline_url", "N/A"),
            "notification_text": busy.wait_message(),
            "error": "ci_busy",
        }

    try:
        from agent.services.git_ops import open_workspace

        # Multi-gate: PR already opened — merge only (no second push/PR).
        if state.get("pr_url") and (state.get("pr_number") or state.get("pr_id")):
            sha = state.get("commit_sha") or ""
            if provider == "azure_devops":
                return await _deploy_azdo(state, task_id, sha, branch_name, user_msg)
            return await _deploy_github(
                state,
                task_id=task_id,
                sha=sha,
                branch_name=branch_name,
                user_msg=user_msg,
                owner=owner,
                repo_name=repo_name,
                workspace_path=workspace_path,
            )

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

        return await _deploy_github(
            state,
            task_id=task_id,
            sha=sha,
            branch_name=branch_name,
            user_msg=user_msg,
            owner=owner,
            repo_name=repo_name,
            workspace_path=workspace_path,
        )

    except Exception as e:
        logger.exception("Deployment failed for task %s", task_id)
        from agent.services.deploy_notify import (
            format_deploy_step_failed,
            send_deploy_progress,
            user_facing_deploy_error,
        )

        phone = state.get("whatsapp_phone", "")
        reason = user_facing_deploy_error(e, step="deployment")
        if phone:
            await send_deploy_progress(
                phone,
                format_deploy_step_failed(task_id, "Push / merge / deploy", reason),
            )
        return {
            **state,
            "status": "failed",
            "error": str(e),
            "notification_text": reason,
        }


async def _deploy_github(
    state: AgentState,
    *,
    task_id: str,
    sha: str,
    branch_name: str,
    user_msg: str,
    owner: str,
    repo_name: str,
    workspace_path: str,
) -> AgentState:
    """GitHub-only path: PR → merge → wait for Actions → Kudu only if CI did not finish."""
    pr_url = state.get("pr_url") or ""
    pr_number = state.get("pr_number")
    if not pr_number:
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
    pipeline_url = f"https://github.com/{owner}/{repo_name}/actions"
    pipeline_status = "skipped"

    merged = False
    live_url = ""
    deploy_error = ""
    is_sample = repo_name == settings.sample_app_github_repo

    if is_sample and pr_number:
        merged, deploy_error = await _merge_github_pr(
            owner=owner,
            repo_name=repo_name,
            pr_number=int(pr_number),
            task_id=task_id,
            user_msg=user_msg,
        )
        if merged:
            # Do NOT manually trigger workflow_dispatch — merge to main already starts Actions.
            ci_ctx = {
                "provider": "github",
                "owner": owner,
                "repo_name": repo_name,
            }
            idle = await wait_for_ci_idle(ci_ctx, timeout_sec=540, poll_sec=15)
            if idle.busy or idle.detail == "no_ci_observed":
                pipeline_status = "timeout" if idle.busy else "no_ci"
                pipeline_url = idle.url or pipeline_url
                logger.warning(
                    "GitHub CI %s for %s/%s — Kudu fallback",
                    pipeline_status,
                    owner,
                    repo_name,
                )
                live_url, kudu_err = await _kudu_sample_fallback(
                    workspace_path, owner, repo_name
                )
                if kudu_err:
                    deploy_error = kudu_err
                elif live_url:
                    pipeline_status = "kudu_fallback"
            else:
                # Actions deploy owns the live app — do not run a second Kudu deploy.
                pipeline_status = "succeeded"
                pipeline_url = idle.url or pipeline_url
                live_url = settings.sample_app_url

    lines = [
        "*Final deployment complete*" if live_url else "*Deployment result*",
        f"*Merged to main:* {'yes' if merged else 'no'}",
        f"*GitHub Actions:* {pipeline_status}",
    ]
    if live_url:
        lines.extend(
            [
                f"*Live app:* {live_url}",
                f"*Docs:* {live_url.rstrip('/')}/docs",
                "Open on your *phone browser* and refresh — no GitHub visit needed.",
            ]
        )
    else:
        lines.append(f"*PR:* {pr_url}")
        if deploy_error:
            lines.append(f"*Live deploy error:* {deploy_error}")
        else:
            lines.append("Live deploy was skipped for this repo.")
    if pipeline_url:
        lines.append(f"*Pipeline:* {pipeline_url}")

    detail = "\n".join(lines)
    logger.info(
        "Deployed GitHub task %s: PR=%s merged=%s live=%s ci=%s",
        task_id,
        pr_url,
        merged,
        live_url or "-",
        pipeline_status,
    )
    return {
        **state,
        "status": "completed" if (merged or pr_url) else "failed",
        "commit_sha": sha,
        "pr_url": pr_url,
        "pipeline_url": pipeline_url or "N/A",
        "pipeline_status": pipeline_status,
        "repo_provider": "github",
        "notification_text": detail,
        "evaluation_text": (
            f"1. Your WhatsApp approval authorized GitHub deployment\n"
            f"2. Changes merged to `main`: {'yes' if merged else 'no'}\n"
            f"3. Live deploy: {live_url or deploy_error or 'skipped'}\n"
            f"4. Pipeline: {pipeline_status} — {pipeline_url or 'N/A'}\n"
            + (
                "5. Done — refresh phone browser to see changes"
                if live_url
                else "5. Code is on GitHub; live app still needs a successful deploy"
            )
        ),
    }


async def _deploy_azdo(
    state: AgentState,
    task_id: str,
    sha: str,
    branch_name: str,
    user_msg: str,
) -> AgentState:
    """Azure DevOps-only path: PR → merge → wait for pipeline → Kudu only if needed."""
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

    pr_id = state.get("pr_id")
    pr_url = state.get("pr_url") or ""
    if not pr_id:
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
    else:
        org = settings.azdo_org_url.rstrip("/")
        if not pr_url:
            pr_url = f"{org}/{project}/_git/{repo_name or repo_id}/pullrequest/{pr_id}"

    # Link only the pipeline that belongs to THIS repo (never pipelines[0] / SmartDocs).
    pipeline_url = f"{org}/{project}/_build"
    try:
        owned = await azdo.find_pipeline_for_repo(project, repo_name or "")
        if owned:
            pipeline_url = (
                f"{org}/{project}/_build?definitionId={owned.get('id')}"
            )
    except Exception:
        logger.warning("Could not resolve owning pipeline for %s/%s", project, repo_name)

    pipeline_status = "skipped"
    merged = False
    live_url = ""
    deploy_error = ""
    is_demo_repo = (repo_name or "").lower() == settings.azdo_demo_repo.lower() or (
        project.lower() == settings.azdo_demo_project.lower()
        and (repo_name or "").lower() == settings.azdo_demo_repo.lower()
    )

    if is_demo_repo and pr_id:
        from agent.services.ci_watch import save_ci_watch, start_azdo_ci_watch
        from agent.services.deploy_notify import (
            format_merge_failed,
            format_merging_pr,
            format_pipeline_watching,
            send_deploy_progress,
            user_facing_deploy_error,
        )

        phone = state.get("whatsapp_phone", "")
        live_url = f"{settings.agent_app_url.rstrip('/')}/portal"

        # Persist BEFORE merge — survives App Service recycle during self-deploy pipeline.
        try:
            await save_ci_watch(
                task_id=task_id,
                phone=phone,
                project=project,
                repo_name=repo_name or "",
                repo_id=str(repo_id),
                pr_url=pr_url,
                pipeline_url=pipeline_url,
                commit_sha=sha,
                live_url=live_url,
                notes="pre_merge",
            )
        except Exception:
            logger.exception("Failed pre-merge CI watch for task %s", task_id)

        await send_deploy_progress(
            phone,
            format_merging_pr(task_id, pr_url, pr_id),
        )

        try:
            merge_result = await asyncio.wait_for(
                azdo.merge_pull_request(project, str(repo_id), int(pr_id)),
                timeout=120,
            )
            merged = True
            src = (merge_result.get("lastMergeSourceCommit") or {}).get("commitId")
            if src:
                sha = src
        except asyncio.TimeoutError:
            merged = False
            deploy_error = user_facing_deploy_error("merge timed out", step="merge to main")
            await send_deploy_progress(
                phone,
                format_merge_failed(task_id, pr_url, deploy_error),
            )
        except Exception as e:
            msg = str(e).lower()
            if "completed" in msg or "already" in msg:
                merged = True
            else:
                logger.exception("AzDO merge failed for task %s", task_id)
                deploy_error = user_facing_deploy_error(e, step="merge to main")
                await send_deploy_progress(
                    phone,
                    format_merge_failed(task_id, pr_url, deploy_error),
                )

        if merged:
            pipeline_status = "watching"
            try:
                await save_ci_watch(
                    task_id=task_id,
                    phone=phone,
                    project=project,
                    repo_name=repo_name or "",
                    repo_id=str(repo_id),
                    pr_url=pr_url,
                    pipeline_url=pipeline_url,
                    commit_sha=sha,
                    live_url=live_url,
                    notes="post_merge",
                )
                await send_deploy_progress(
                    phone,
                    format_pipeline_watching(task_id, pipeline_url),
                )
                start_azdo_ci_watch(task_id)
            except Exception:
                logger.exception(
                    "Failed post-merge CI watch for task %s", task_id
                )
                try:
                    base = await deploy_agent_app_from_workspace(
                        state.get("workspace_path", "")
                    )
                    live_url = f"{base.rstrip('/')}/portal"
                    pipeline_status = "kudu_fallback"
                except Exception as e:
                    logger.exception("AzDO Kudu fallback failed for task %s", task_id)
                    deploy_error = user_facing_deploy_error(e, step="live deploy")
                    pipeline_status = "failed"
                    await send_deploy_progress(
                        phone,
                        format_merge_failed(task_id, pr_url, deploy_error),
                    )

    if pipeline_status == "watching":
        detail = (
            "*Merge complete — pipeline running*\n"
            f"*Merged to main:* yes\n"
            f"*Azure Pipelines:* watching\n"
            f"*Pipeline:* {pipeline_url}\n"
            "You will get a WhatsApp *Final evaluation* when the pipeline finishes."
        )
        # Final evaluation is sent by durable ci_watch after real CI outcome — not here.
        evaluation = ""
    elif live_url:
        detail = (
            "*Final deployment complete*\n"
            f"*Merged to main:* yes\n"
            f"*Azure Pipelines:* {pipeline_status}\n"
            f"*Live portal:* {live_url}\n"
            f"*Pipeline:* {pipeline_url}\n"
            "Open on your *phone browser* and refresh — no Azure DevOps visit needed."
        )
        evaluation = (
            "1. Your WhatsApp approval authorized Azure DevOps deployment\n"
            f"2. Changes merged to `main`: {'yes' if merged else 'no'}\n"
            f"3. Live portal: {live_url}\n"
            f"4. Pipeline: {pipeline_status} — {pipeline_url}\n"
            "5. Done — refresh phone browser on /portal"
        )
    else:
        detail = (
            f"*Azure DevOps PR:* {pr_url}\n"
            f"*Merged to main:* {'yes' if merged else 'no'}\n"
            f"*Azure Pipelines:* {pipeline_status}\n"
        )
        if deploy_error:
            detail += f"*Live deploy error:* {deploy_error}"
        else:
            detail += "Live deploy runs via Azure Pipelines after merge to main."
        evaluation = (
            "1. Your WhatsApp approval authorized Azure DevOps deployment\n"
            f"2. Changes merged to `main`: {'yes' if merged else 'no'}\n"
            f"3. Live portal: {live_url or deploy_error or 'skipped'}\n"
            f"4. Pipeline: {pipeline_status} — {pipeline_url}\n"
            "5. PR created; live portal still needs a successful deploy"
        )

    final_status = "failed"
    if pipeline_status == "watching":
        final_status = "pipeline_watching"
    elif merged or pipeline_status in ("succeeded", "kudu_fallback"):
        final_status = "completed"
    elif deploy_error:
        final_status = "failed"
    elif pr_url and not is_demo_repo:
        final_status = "completed"

    return {
        **state,
        "status": final_status,
        "commit_sha": sha,
        "pr_url": pr_url,
        "pipeline_url": pipeline_url,
        "pipeline_status": pipeline_status,
        "repo_provider": "azure_devops",
        "azdo_project": project,
        "azdo_repo_id": str(repo_id),
        "repo_name": repo_name,
        "notification_text": detail,
        "evaluation_text": evaluation,
    }
