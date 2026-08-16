import base64

import httpx

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)


def _headers() -> dict:
    pat = settings.azdo_pat.get_secret_value()
    encoded = base64.b64encode(f":{pat}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


def _org_url() -> str:
    return settings.azdo_org_url.rstrip("/")


def _org_api(path: str) -> str:
    """Org-level API: https://dev.azure.com/{org}/_apis/..."""
    return f"{_org_url()}/_apis/{path.lstrip('/')}"


def _project_api(project: str, path: str) -> str:
    """Project-level API: https://dev.azure.com/{org}/{project}/_apis/..."""
    return f"{_org_url()}/{project}/_apis/{path.lstrip('/')}"


async def list_projects() -> list[dict]:
    url = _org_api("projects?api-version=7.1")
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json().get("value", [])


async def list_repositories(project: str) -> list[dict]:
    url = _project_api(project, "git/repositories?api-version=7.1")
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        if resp.status_code >= 400:
            logger.error("AzDO list_repositories failed: %s %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        return resp.json().get("value", [])


async def create_pull_request(
    project: str,
    repo_id: str,
    title: str,
    description: str,
    source_branch: str,
    target_branch: str = "refs/heads/main",
) -> dict:
    url = _project_api(
        project,
        f"git/repositories/{repo_id}/pullrequests?api-version=7.1",
    )
    src = source_branch if source_branch.startswith("refs/") else f"refs/heads/{source_branch}"
    tgt = target_branch if target_branch.startswith("refs/") else f"refs/heads/{target_branch}"
    payload = {
        "sourceRefName": src,
        "targetRefName": tgt,
        "title": title,
        "description": description,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers(), timeout=30)
        resp.raise_for_status()
        pr = resp.json()
        logger.info("Created Azure DevOps PR #%s", pr.get("pullRequestId"))
        return pr


async def list_pipelines(project: str) -> list[dict]:
    url = _project_api(project, "pipelines?api-version=7.1")
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json().get("value", [])


async def find_pipeline_for_repo(project: str, repo_name: str) -> dict | None:
    """Resolve the pipeline that belongs to a repo — never use pipelines[0]."""
    if not repo_name:
        return None
    target = repo_name.strip().lower()
    pipelines = await list_pipelines(project)
    exact = next((p for p in pipelines if (p.get("name") or "").lower() == target), None)
    if exact:
        return exact
    # Prefer name containment only when unambiguous
    partial = [
        p
        for p in pipelines
        if target in (p.get("name") or "").lower()
        or (p.get("name") or "").lower() in target
    ]
    if len(partial) == 1:
        return partial[0]
    if partial:
        logger.warning(
            "Ambiguous AzDO pipelines for repo %s: %s — refusing to guess",
            repo_name,
            [p.get("name") for p in partial],
        )
    return None


async def trigger_pipeline(
    project: str,
    pipeline_id: int,
    branch: str = "main",
    *,
    expected_repo_name: str | None = None,
) -> dict:
    """Trigger a pipeline run. Refuses cross-repo triggers when expected_repo_name is set.

    Note: AzDO UI labels REST-triggered runs as 'Manually run' by the PAT owner.
    Prefer merge-triggered CI for repo deploys; do not call with pipelines[0].
    """
    if expected_repo_name:
        match = await find_pipeline_for_repo(project, expected_repo_name)
        if not match or int(match.get("id") or -1) != int(pipeline_id):
            raise RuntimeError(
                f"Refusing to trigger pipeline id={pipeline_id} for project {project}: "
                f"it does not match repo '{expected_repo_name}' "
                f"(resolved={match.get('name') if match else None}, id={match.get('id') if match else None}). "
                "This guard prevents cross-triggering pipelines like Linux.SmartDocs-WebApp."
            )

    url = _project_api(project, f"pipelines/{pipeline_id}/runs?api-version=7.1")
    payload = {
        "resources": {
            "repositories": {
                "self": {"refName": f"refs/heads/{branch}"}
            }
        }
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers(), timeout=30)
        resp.raise_for_status()
        run = resp.json()
        logger.info(
            "Triggered Azure DevOps pipeline run #%s (definition=%s)",
            run.get("id"),
            pipeline_id,
        )
        return run


async def list_builds(
    project: str,
    *,
    status_filter: str = "1,32",
    top: int = 10,
    repository_id: str | None = None,
    definition_ids: str | None = None,
) -> list[dict]:
    """List AzDO builds. statusFilter: 1=inProgress, 2=completed, 32=notStarted."""
    qs = f"build/builds?statusFilter={status_filter}&$top={top}&api-version=7.1"
    if repository_id:
        qs += f"&repositoryId={repository_id}&repositoryType=TfsGit"
    if definition_ids:
        qs += f"&definitions={definition_ids}"
    url = _project_api(project, qs)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json().get("value", [])


async def list_active_builds(
    project: str,
    *,
    repository_id: str | None = None,
    repo_name: str | None = None,
) -> list[dict]:
    """Return in-progress / not-started builds scoped to ONE repo (never project-wide)."""
    repo_id = repository_id
    if not repo_id and repo_name:
        try:
            repo = await get_repository(project, repo_name)
            repo_id = repo.get("id")
        except Exception:
            logger.warning("Could not resolve AzDO repo %s in %s", repo_name, project)

    # Safety: without a repo scope we must not report SmartDocs/other project pipelines
    if not repo_id and not repo_name:
        logger.warning(
            "list_active_builds called without repo scope for project %s — returning empty",
            project,
        )
        return []

    try:
        builds = await list_builds(
            project, status_filter="1,32", top=15, repository_id=repo_id
        )
    except Exception:
        logger.warning("Failed listing AzDO builds for project %s", project)
        return []

    active_states = {"inProgress", "notStarted", "cancelling", "postponed"}
    wanted = (repo_name or "").strip().lower()
    out: list[dict] = []
    for b in builds:
        status = (b.get("status") or "").replace(" ", "")
        if status not in active_states and str(b.get("status")) not in ("1", "32", "4", "8"):
            continue
        repo_ref = ((b.get("repository") or {}).get("name") or "").lower()
        if wanted and repo_ref and repo_ref != wanted:
            continue
        if repo_id:
            rid = (b.get("repository") or {}).get("id")
            if rid and str(rid) != str(repo_id):
                continue
        out.append(b)
    return out


async def get_pull_request(project: str, repo_id: str, pr_id: int) -> dict:
    url = _project_api(
        project,
        f"git/repositories/{repo_id}/pullrequests/{pr_id}?api-version=7.1",
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()


async def get_pull_request_commits(project: str, repo_id: str, pr_id: int) -> list[dict]:
    url = _project_api(
        project,
        f"git/repositories/{repo_id}/pullrequests/{pr_id}/commits?api-version=7.1",
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        if resp.status_code >= 400:
            logger.error("AzDO PR commits failed: %s %s", resp.status_code, resp.text[:300])
            return []
        return resp.json().get("value") or []


async def get_pull_request_threads(project: str, repo_id: str, pr_id: int) -> list[dict]:
    """PR discussion threads (review comments)."""
    url = _project_api(
        project,
        f"git/repositories/{repo_id}/pullrequests/{pr_id}/threads?api-version=7.1",
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        if resp.status_code >= 400:
            logger.error("AzDO PR threads failed: %s %s", resp.status_code, resp.text[:300])
            return []
        return resp.json().get("value") or []


async def get_pull_request_changes(project: str, repo_id: str, pr_id: int) -> list[dict]:
    """Changed files on the latest PR iteration (best-effort)."""
    # Resolve latest iteration
    iter_url = _project_api(
        project,
        f"git/repositories/{repo_id}/pullrequests/{pr_id}/iterations?api-version=7.1",
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(iter_url, headers=_headers(), timeout=30)
        if resp.status_code >= 400:
            logger.error("AzDO PR iterations failed: %s %s", resp.status_code, resp.text[:300])
            return []
        iterations = resp.json().get("value") or []
        if not iterations:
            return []
        latest = max(iterations, key=lambda i: int(i.get("id") or 0))
        it_id = latest.get("id")
        ch_url = _project_api(
            project,
            f"git/repositories/{repo_id}/pullrequests/{pr_id}/iterations/{it_id}/changes"
            f"?api-version=7.1",
        )
        ch = await client.get(ch_url, headers=_headers(), timeout=60)
        if ch.status_code >= 400:
            logger.error("AzDO PR changes failed: %s %s", ch.status_code, ch.text[:300])
            return []
        change_entries = ch.json().get("changeEntries") or ch.json().get("value") or []
        return change_entries if isinstance(change_entries, list) else []


async def find_pull_request_across_repos(
    project: str,
    pr_id: int,
    *,
    preferred_repo: str | None = None,
) -> tuple[dict, dict] | None:
    """Locate PR by id; returns (repo, pr) or None."""
    repos = await list_repositories(project)
    preferred = (preferred_repo or settings.azdo_demo_repo or "").strip().lower()
    ordered = sorted(
        repos,
        key=lambda r: 0 if (r.get("name") or "").lower() == preferred else 1,
    )
    for repo in ordered:
        rid = repo.get("id")
        if not rid:
            continue
        try:
            pr = await get_pull_request(project, rid, pr_id)
            if pr and pr.get("pullRequestId"):
                return repo, pr
        except Exception:
            continue
    return None


async def find_active_pr_for_branch(
    project: str,
    repo_id: str,
    branch: str,
) -> dict | None:
    """Find an active (not abandoned/completed) PR whose source is this branch."""
    src = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    url = _project_api(
        project,
        f"git/repositories/{repo_id}/pullrequests"
        f"?searchCriteria.status=active"
        f"&searchCriteria.sourceRefName={src}"
        f"&api-version=7.1",
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        if resp.status_code >= 400:
            logger.error(
                "AzDO find_active_pr_for_branch failed: %s %s",
                resp.status_code,
                resp.text[:300],
            )
            resp.raise_for_status()
        items = resp.json().get("value") or []
        return items[0] if items else None


async def get_build_timeline(project: str, build_id: str | int) -> list[dict]:
    url = _project_api(
        project,
        f"build/builds/{build_id}/timeline?api-version=7.1",
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        if resp.status_code >= 400:
            logger.warning(
                "AzDO timeline failed build=%s: %s", build_id, resp.status_code
            )
            return []
        return resp.json().get("records") or []


async def get_build_log_content(
    project: str, build_id: str | int, log_id: str | int, *, max_chars: int = 12000
) -> str:
    url = _project_api(
        project,
        f"build/builds/{build_id}/logs/{log_id}?api-version=7.1",
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=60)
        if resp.status_code >= 400:
            return ""
        text = resp.text or ""
        if len(text) > max_chars:
            return text[-max_chars:]
        return text


async def get_build_failure_summary(
    project: str, build_id: str | int, *, max_chars: int = 14000
) -> str:
    """Extract failed-step logs (prefer pytest / Run tests) for the test_fixer agent."""
    records = await get_build_timeline(project, build_id)
    failed = [
        r
        for r in records
        if (r.get("result") or "").lower() == "failed"
        and (r.get("type") or "").lower() in ("task", "job", "")
    ]
    if not failed:
        failed = [r for r in records if (r.get("result") or "").lower() == "failed"]

    def _score(rec: dict) -> int:
        name = (rec.get("name") or "").lower()
        score = 0
        if "test" in name:
            score += 10
        if "pytest" in name or "run tests" in name:
            score += 20
        return score

    failed.sort(key=_score, reverse=True)
    chunks: list[str] = []
    for rec in failed[:4]:
        name = rec.get("name") or "step"
        log_id = rec.get("log", {}).get("id") if isinstance(rec.get("log"), dict) else None
        header = f"=== FAILED: {name} ==="
        if not log_id:
            chunks.append(f"{header}\n(no log id)")
            continue
        body = await get_build_log_content(project, build_id, log_id, max_chars=8000)
        # Prefer the pytest summary tail when present
        lower = body.lower()
        if "failed" in lower and ("pytest" in lower or "====" in body):
            idx = lower.rfind("short test summary")
            if idx < 0:
                idx = lower.rfind("failed ")
            if idx > 0:
                body = body[idx:]
        chunks.append(f"{header}\n{body}")
        if sum(len(c) for c in chunks) >= max_chars:
            break

    summary = "\n\n".join(chunks).strip()
    if len(summary) > max_chars:
        summary = summary[-max_chars:]
    return summary or f"Build {build_id} failed (no timeline logs available)."


async def merge_pull_request(
    project: str,
    repo_id: str,
    pr_id: int,
    *,
    merge_strategy: str = "squash",
) -> dict:
    """Complete (merge) an Azure DevOps pull request into its target branch."""
    pr = await get_pull_request(project, repo_id, pr_id)
    status = (pr.get("status") or "").lower()
    if status == "completed":
        logger.info("AzDO PR #%s already completed", pr_id)
        return pr

    source_commit = pr.get("lastMergeSourceCommit") or {}
    if not source_commit.get("commitId"):
        raise RuntimeError(f"AzDO PR #{pr_id} missing lastMergeSourceCommit")

    url = _project_api(
        project,
        f"git/repositories/{repo_id}/pullrequests/{pr_id}?api-version=7.1",
    )
    payload = {
        "status": "completed",
        "lastMergeSourceCommit": {"commitId": source_commit["commitId"]},
        "completionOptions": {
            "mergeStrategy": merge_strategy,
            "deleteSourceBranch": False,
        },
    }
    async with httpx.AsyncClient() as client:
        resp = await client.patch(url, json=payload, headers=_headers(), timeout=60)
        if resp.status_code >= 400:
            logger.error("AzDO merge failed: %s %s", resp.status_code, resp.text[:400])
        resp.raise_for_status()
        result = resp.json()
        logger.info("Merged Azure DevOps PR #%s", pr_id)
        return result


async def get_repository(project: str, repo_name_or_id: str) -> dict:
    repos = await list_repositories(project)
    match = next(
        (
            r
            for r in repos
            if r.get("id") == repo_name_or_id or r.get("name") == repo_name_or_id
        ),
        None,
    )
    if not match:
        raise RuntimeError(f"AzDO repo not found: {repo_name_or_id}")
    return match


async def download_repo_zip(
    project: str,
    repo_id: str,
    branch: str = "main",
) -> bytes:
    """Download a branch as zip (no system git required)."""
    url = _project_api(
        project,
        (
            f"git/repositories/{repo_id}/items"
            f"?path=/&versionDescriptor.versionType=branch"
            f"&versionDescriptor.version={branch}"
            f"&$format=zip&api-version=7.1"
        ),
    )
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code >= 400:
            logger.error("AzDO zip download failed: %s %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        return resp.content


async def get_branch_commit_sha(project: str, repo_id: str, branch: str = "main") -> str:
    ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    url = _project_api(
        project,
        f"git/repositories/{repo_id}/refs?filter=heads/{branch}&api-version=7.1",
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        values = resp.json().get("value") or []
        if not values:
            # try exact filter
            url = _project_api(
                project,
                f"git/repositories/{repo_id}/refs?filter={ref.replace('refs/', '')}&api-version=7.1",
            )
            resp = await client.get(url, headers=_headers(), timeout=30)
            resp.raise_for_status()
            values = resp.json().get("value") or []
        if not values:
            raise RuntimeError(f"AzDO branch not found: {branch}")
        return values[0]["objectId"]


async def push_commit(
    project: str,
    repo_id: str,
    branch: str,
    message: str,
    changes: list[dict],
    old_object_id: str,
) -> str:
    """Create/update a branch with a commit via AzDO pushes API. Returns new commit SHA."""
    ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    url = _project_api(project, f"git/repositories/{repo_id}/pushes?api-version=7.1")
    payload = {
        "refUpdates": [{"name": ref, "oldObjectId": old_object_id}],
        "commits": [
            {
                "comment": message,
                "changes": changes,
            }
        ],
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers(), timeout=120)
        if resp.status_code >= 400:
            logger.error("AzDO push failed: %s %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        data = resp.json()
        commits = data.get("commits") or []
        sha = commits[0]["commitId"] if commits else ""
        logger.info("Pushed AzDO commit %s to %s", sha[:8] if sha else "?", branch)
        return sha