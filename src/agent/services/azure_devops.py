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


async def trigger_pipeline(project: str, pipeline_id: int, branch: str = "main") -> dict:
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
        logger.info("Triggered Azure DevOps pipeline run #%s", run.get("id"))
        return run


async def get_pull_request(project: str, repo_id: str, pr_id: int) -> dict:
    url = _project_api(
        project,
        f"git/repositories/{repo_id}/pullrequests/{pr_id}?api-version=7.1",
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()


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