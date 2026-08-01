"""Deploy demo sample app to Azure App Service via Kudu zipdeploy."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx

from agent.config import settings
from agent.core.logging import get_logger
from agent.services.github import GITHUB_API, _headers

logger = get_logger(__name__)


def _zip_workspace(workspace: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(workspace)
            if any(p in {".git", ".venv", "venv", "__pycache__"} for p in rel.parts):
                continue
            if rel.name == ".agent_workspace.json":
                continue
            zf.write(path, rel.as_posix())
    return buf.getvalue()


def _ensure_requirements(payload: bytes) -> bytes:
    """Ensure requirements.txt exists in the zip so Oryx can install deps."""
    with zipfile.ZipFile(io.BytesIO(payload), "r") as zin:
        names = set(zin.namelist())
        if any(n.endswith("requirements.txt") for n in names):
            return payload
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
            zout.writestr(
                "requirements.txt",
                "fastapi>=0.115\nuvicorn[standard]>=0.30\n",
            )
        return buf.getvalue()


async def _kudu_zipdeploy(
    payload: bytes,
    *,
    app_name: str | None = None,
    live_url: str | None = None,
    user: str | None = None,
    password: str | None = None,
    ensure_requirements: bool = True,
) -> str:
    app_name = app_name or settings.sample_app_name
    live_url = (live_url or settings.sample_app_url).rstrip("/")
    user = user if user is not None else settings.sample_app_publish_user
    password = (
        password
        if password is not None
        else settings.sample_app_publish_password.get_secret_value()
    )

    if not user or not password:
        raise RuntimeError(
            f"Publish credentials not configured for app {app_name}"
        )

    if ensure_requirements:
        payload = _ensure_requirements(payload)
    scm = f"https://{app_name}.scm.azurewebsites.net/api/zipdeploy"
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            scm,
            content=payload,
            headers={"Content-Type": "application/zip"},
            auth=(user, password),
        )
        if resp.status_code >= 400:
            logger.error("zipdeploy failed (%s): %s %s", app_name, resp.status_code, resp.text[:400])
            if resp.status_code == 401:
                raise RuntimeError(
                    f"Live deploy 401 Unauthorized for {app_name}. "
                    "Enable SCM basic auth and refresh publish credentials."
                )
            resp.raise_for_status()

    logger.info("App redeployed to %s (%d bytes)", live_url, len(payload))
    return live_url


async def deploy_sample_app_from_workspace(workspace_path: str) -> str:
    """Zip workspace and push to the demo App Service. Returns live URL."""
    workspace = Path(workspace_path)
    if not workspace.exists():
        raise RuntimeError(f"Workspace not found: {workspace_path}")
    return await _kudu_zipdeploy(_zip_workspace(workspace))


async def deploy_agent_app_from_workspace(workspace_path: str) -> str:
    """Redeploy the WhatsApp agent App Service from an AzDO workspace."""
    workspace = Path(workspace_path)
    if not workspace.exists():
        raise RuntimeError(f"Workspace not found: {workspace_path}")
    return await _kudu_zipdeploy(
        _zip_workspace(workspace),
        app_name=settings.agent_app_name,
        live_url=settings.agent_app_url,
        user=settings.agent_app_publish_user,
        password=settings.agent_app_publish_password.get_secret_value(),
        ensure_requirements=True,
    )


async def deploy_sample_app_from_github(owner: str, repo: str, ref: str = "main") -> str:
    """Download GitHub zipball and deploy to the demo App Service."""
    headers = _headers()
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/zipball/{ref}",
            headers=headers,
        )
        resp.raise_for_status()
        raw = resp.content

    # GitHub zipball nests files under owner-repo-sha/; flatten to app root
    flat = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zin, zipfile.ZipFile(
        flat, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        root_prefix = zin.namelist()[0].split("/")[0]
        for info in zin.infolist():
            if info.is_dir():
                continue
            rel = Path(info.filename).relative_to(root_prefix).as_posix()
            if not rel or rel.startswith(".git"):
                continue
            zout.writestr(rel, zin.read(info.filename))

    return await _kudu_zipdeploy(flat.getvalue())
