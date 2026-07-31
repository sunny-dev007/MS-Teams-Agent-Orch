import httpx

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)


def _headers() -> dict:
    import base64
    pat = settings.azdo_pat.get_secret_value()
    encoded = base64.b64encode(f":{pat}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


def _api_url(path: str) -> str:
    return f"{settings.azdo_org_url}/_apis/{path}"


async def list_projects() -> list[dict]:
    url = _api_url("projects?api-version=7.1")
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
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
    url = _api_url(f"{project}/_apis/git/repositories/{repo_id}/pullrequests?api-version=7.1")
    payload = {
        "sourceRefName": f"refs/heads/{source_branch}" if not source_branch.startswith("refs/") else source_branch,
        "targetRefName": target_branch,
        "title": title,
        "description": description,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers(), timeout=30)
        resp.raise_for_status()
        pr = resp.json()
        logger.info("Created Azure DevOps PR #%s", pr.get("pullRequestId"))
        return pr


async def trigger_pipeline(project: str, pipeline_id: int, branch: str = "main") -> dict:
    url = _api_url(f"{project}/_apis/pipelines/{pipeline_id}/runs?api-version=7.1")
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


async def list_pipelines(project: str) -> list[dict]:
    url = _api_url(f"{project}/_apis/pipelines?api-version=7.1")
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json().get("value", [])
