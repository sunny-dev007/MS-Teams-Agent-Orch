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
