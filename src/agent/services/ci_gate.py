"""Feature: CI gate — isolate GitHub / Azure DevOps deploy locks and busy checks.

Blocks WhatsApp final approval (and same-repo coding requests) while a provider
pipeline is already running, so we do not start duplicate builds/deploys.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

# In-process deploy locks, keyed per provider+repo (never shared across providers).
_active_deploys: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()


@dataclass(frozen=True)
class CiBusyInfo:
    busy: bool
    provider: str = ""
    label: str = ""
    url: str = ""
    detail: str = ""

    def wait_message(self) -> str:
        target = self.label or "this repository"
        link = f"\n{self.url}" if self.url else ""
        return (
            f"*Please wait* — a *{self.provider or 'CI'}* pipeline is still running "
            f"for `{target}`.\n\n"
            f"{self.detail or 'Try the same request again after it finishes.'}"
            f"{link}\n\n"
            "I will not start another merge/deploy until that pipeline completes."
        )


def deploy_lock_key(
    provider: str,
    *,
    owner: str = "",
    repo_name: str = "",
    project: str = "",
) -> str:
    """Stable lock key — GitHub and Azure DevOps never share a key."""
    p = (provider or "").lower().strip()
    if p in ("azure_devops", "azdo", "ado"):
        proj = (project or settings.azdo_demo_project or "").strip().lower()
        repo = (repo_name or "").strip().lower()
        return f"azure_devops:{proj}/{repo}"
    owner_s = (owner or settings.github_default_owner or "").strip().lower()
    repo = (repo_name or "").strip().lower()
    return f"github:{owner_s}/{repo}"


def context_from_session_data(data: dict | None) -> dict[str, str]:
    data = data or {}
    provider = (data.get("repo_provider") or "").lower().strip()
    return {
        "provider": provider,
        "owner": str(data.get("repo_owner") or ""),
        "repo_name": str(data.get("repo_name") or ""),
        "repo_url": str(data.get("repo_url") or ""),
        "project": str(data.get("azdo_project") or ""),
        "repo_id": str(data.get("azdo_repo_id") or ""),
    }


async def try_acquire_deploy(key: str, task_id: str) -> bool:
    if not key or key.endswith("/") or key.endswith(":"):
        return True
    async with _lock:
        current = _active_deploys.get(key)
        if current and current.get("task_id") != task_id:
            # Stale lock safety: auto-expire after 45 minutes
            if time.time() - float(current.get("started_at", 0)) < 45 * 60:
                return False
        _active_deploys[key] = {"task_id": task_id, "started_at": time.time()}
        return True


async def release_deploy(key: str, task_id: str) -> None:
    if not key:
        return
    async with _lock:
        current = _active_deploys.get(key)
        if current and current.get("task_id") == task_id:
            _active_deploys.pop(key, None)


async def get_active_deploy(key: str) -> dict[str, Any] | None:
    async with _lock:
        current = _active_deploys.get(key)
        if not current:
            return None
        if time.time() - float(current.get("started_at", 0)) >= 45 * 60:
            _active_deploys.pop(key, None)
            return None
        return dict(current)


async def check_github_ci_busy(
    owner: str,
    repo: str,
    *,
    branch: str | None = "main",
) -> CiBusyInfo:
    from agent.services.github import list_active_workflow_runs

    if not owner or not repo:
        return CiBusyInfo(busy=False, provider="github")

    try:
        runs = await list_active_workflow_runs(owner, repo, branch=branch)
    except Exception:
        logger.exception("GitHub CI busy check failed for %s/%s", owner, repo)
        return CiBusyInfo(busy=False, provider="github")

    if not runs:
        # Also check without branch filter (workflow_dispatch / PR runs)
        try:
            runs = await list_active_workflow_runs(owner, repo, branch=None)
        except Exception:
            runs = []

    if not runs:
        return CiBusyInfo(busy=False, provider="github", label=f"{owner}/{repo}")

    run = runs[0]
    url = run.get("html_url") or f"https://github.com/{owner}/{repo}/actions"
    name = run.get("name") or "workflow"
    status = run.get("status") or "in_progress"
    return CiBusyInfo(
        busy=True,
        provider="GitHub Actions",
        label=f"{owner}/{repo}",
        url=url,
        detail=f"Active run: *{name}* (`{status}`). {len(runs)} active run(s).",
    )


async def check_azdo_ci_busy(
    project: str,
    *,
    repo_name: str = "",
    repo_id: str = "",
) -> CiBusyInfo:
    from agent.services import azure_devops as azdo

    if not project:
        return CiBusyInfo(busy=False, provider="azure_devops")

    try:
        builds = await azdo.list_active_builds(
            project,
            repository_id=repo_id or None,
            repo_name=repo_name or None,
        )
    except Exception:
        logger.exception("AzDO CI busy check failed for %s/%s", project, repo_name)
        return CiBusyInfo(busy=False, provider="azure_devops")

    if not builds:
        return CiBusyInfo(
            busy=False,
            provider="azure_devops",
            label=f"{project}/{repo_name}" if repo_name else project,
        )

    build = builds[0]
    build_id = build.get("id")
    defn = (build.get("definition") or {}).get("name") or "pipeline"
    status = build.get("status") or "inProgress"
    org = settings.azdo_org_url.rstrip("/")
    url = (
        f"{org}/{project}/_build/results?buildId={build_id}"
        if build_id
        else f"{org}/{project}/_build"
    )
    label = f"{project}/{repo_name}" if repo_name else project
    return CiBusyInfo(
        busy=True,
        provider="Azure Pipelines",
        label=label,
        url=url,
        detail=f"Active build: *{defn}* (`{status}`). {len(builds)} active build(s).",
    )


async def check_ci_busy_for_context(ctx: dict[str, str]) -> CiBusyInfo:
    """Provider-isolated busy check from session / deploy context."""
    provider = (ctx.get("provider") or "").lower().strip()
    repo_url = ctx.get("repo_url") or ""

    if not provider:
        if "dev.azure.com" in repo_url or "visualstudio.com" in repo_url:
            provider = "azure_devops"
        elif repo_url or ctx.get("owner"):
            provider = "github"

    if provider in ("azure_devops", "azdo", "ado"):
        project = ctx.get("project") or ""
        repo_name = ctx.get("repo_name") or ""
        if not project and repo_url:
            try:
                parts = repo_url.rstrip("/").split("/")
                if "_git" in parts:
                    gi = parts.index("_git")
                    project = parts[gi - 1]
                    repo_name = repo_name or parts[gi + 1]
            except Exception:
                pass
        project = project or settings.azdo_demo_project
        return await check_azdo_ci_busy(
            project,
            repo_name=repo_name,
            repo_id=ctx.get("repo_id") or "",
        )

    owner = ctx.get("owner") or ""
    repo = ctx.get("repo_name") or ""
    if (not owner or not repo) and repo_url:
        try:
            from agent.services.github import parse_repo_url

            owner, repo = parse_repo_url(repo_url)
        except Exception:
            pass
    owner = owner or settings.github_default_owner
    if not owner or not repo:
        return CiBusyInfo(busy=False)
    return await check_github_ci_busy(owner, repo)


async def check_deploy_blocked(ctx: dict[str, str], task_id: str | None = None) -> CiBusyInfo:
    """True when another agent deploy is in-flight OR provider CI is running."""
    key = deploy_lock_key(
        ctx.get("provider") or "",
        owner=ctx.get("owner") or "",
        repo_name=ctx.get("repo_name") or "",
        project=ctx.get("project") or "",
    )
    active = await get_active_deploy(key)
    if active and (not task_id or active.get("task_id") != task_id):
        return CiBusyInfo(
            busy=True,
            provider=(ctx.get("provider") or "deploy").replace("_", " "),
            label=key.split(":", 1)[-1],
            detail=(
                f"An agent deploy for task `{active.get('task_id')}` is still in progress. "
                "Please wait and try again shortly."
            ),
        )

    return await check_ci_busy_for_context(ctx)


async def wait_for_ci_idle(
    ctx: dict[str, str],
    *,
    timeout_sec: int = 600,
    poll_sec: int = 15,
    appear_grace_sec: int = 90,
) -> CiBusyInfo:
    """Poll until merge-triggered CI finishes, or conclude none started.

    - Waits briefly for a run to appear after merge
    - If a run is seen, waits until it becomes idle
    - If no run appears within appear_grace_sec, returns idle (caller may Kudu-fallback)
    """
    started = time.time()
    deadline = started + timeout_sec
    last = CiBusyInfo(busy=False)
    saw_busy = False

    await asyncio.sleep(min(12, poll_sec))
    while time.time() < deadline:
        last = await check_ci_busy_for_context(ctx)
        if last.busy:
            saw_busy = True
            logger.info("Waiting for CI idle: %s — %s", last.label, last.detail)
        elif saw_busy:
            logger.info("CI became idle for %s", last.label or ctx)
            return last
        elif (time.time() - started) >= appear_grace_sec:
            logger.info(
                "No CI run appeared within %ss for %s — caller may use Kudu fallback",
                appear_grace_sec,
                ctx,
            )
            return CiBusyInfo(
                busy=False,
                provider=last.provider,
                label=last.label,
                detail="no_ci_observed",
            )
        await asyncio.sleep(poll_sec)

    # Timed out while still busy
    if last.busy:
        return last
    return await check_ci_busy_for_context(ctx)
