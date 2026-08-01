import httpx

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

GITHUB_API = "https://api.github.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str = "main",
) -> dict:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    payload = {"title": title, "body": body, "head": head, "base": base}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers(), timeout=30)
        resp.raise_for_status()
        pr = resp.json()
        logger.info("Created PR #%s: %s", pr["number"], pr["html_url"])
        return pr


async def trigger_workflow(owner: str, repo: str, workflow_id: str, ref: str) -> bool:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
    payload = {"ref": ref}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers(), timeout=30)
        if resp.status_code == 204:
            logger.info("Triggered workflow %s on %s", workflow_id, ref)
            return True
        logger.warning("Failed to trigger workflow: %s", resp.text)
        return False


async def get_repo_info(owner: str, repo: str) -> dict:
    url = f"{GITHUB_API}/repos/{owner}/{repo}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()


async def list_workflows(owner: str, repo: str) -> list[dict]:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/workflows"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json().get("workflows", [])


def parse_repo_url(url: str) -> tuple[str, str]:
    url = url.rstrip("/").removesuffix(".git")
    parts = url.split("/")
    return parts[-2], parts[-1]


async def list_repos(per_page: int = 20) -> list[dict]:
    """List repos for the authenticated user, preferring GITHUB_DEFAULT_OWNER."""
    owner = settings.github_default_owner.strip()
    async with httpx.AsyncClient() as client:
        if owner:
            url = f"{GITHUB_API}/users/{owner}/repos"
            params = {"sort": "updated", "per_page": per_page, "type": "all"}
            resp = await client.get(url, headers=_headers(), params=params, timeout=30)
            if resp.status_code == 404:
                url = f"{GITHUB_API}/orgs/{owner}/repos"
                resp = await client.get(url, headers=_headers(), params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()

        url = f"{GITHUB_API}/user/repos"
        params = {"sort": "updated", "per_page": per_page, "affiliation": "owner,collaborator"}
        resp = await client.get(url, headers=_headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
