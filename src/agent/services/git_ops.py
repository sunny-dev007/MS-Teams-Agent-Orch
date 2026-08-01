"""Local git helpers with HTTP fallback when system git is unavailable.

Azure App Service Python images often ship without `git`. GitPython then fails
on clone. For GitHub repos we download a zipball and push commits via the
Git Data API so coding tasks still work.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from agent.config import settings
from agent.core.logging import get_logger
from agent.services.github import GITHUB_API, _headers, parse_repo_url

logger = get_logger(__name__)
META_FILE = ".agent_workspace.json"


@dataclass
class WorkspaceRepo:
    """Lightweight stand-in for GitPython Repo when system git is missing."""

    path: Path
    provider: str
    owner: str = ""
    repo_name: str = ""
    base_branch: str = "main"
    branch_name: str = ""
    base_commit_sha: str = ""
    base_tree_sha: str = ""
    azdo_project: str = ""
    azdo_repo_id: str = ""
    branch_tip: str = ""
    file_hashes: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def working_dir(self) -> str:
        return str(self.path)


def _import_repo():
    # Keep import lazy so general WhatsApp chat works without GitPython init.
    os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")
    from git import Repo

    return Repo


def _has_system_git() -> bool:
    return shutil.which("git") is not None


def get_workspace_path(task_id: str) -> Path:
    path = Path(settings.workspace_dir) / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def clone_repo(repo_url: str, task_id: str, branch: str = "main"):
    workspace = get_workspace_path(task_id)
    repo_dir = workspace / "repo"

    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    if _has_system_git():
        return _clone_with_git(repo_url, repo_dir, branch)

    if "github.com" in repo_url:
        logger.warning("system git missing — cloning GitHub repo via zipball API")
        return _clone_github_zipball(repo_url, repo_dir, branch)

    if "dev.azure.com" in repo_url or "visualstudio.com" in repo_url:
        logger.warning("system git missing — cloning Azure DevOps repo via zip API")
        return _clone_azdo_zip(repo_url, repo_dir, branch)

    raise RuntimeError(
        "system `git` is not installed on this host, and HTTP clone fallback "
        "is only available for GitHub / Azure DevOps repos."
    )


def _clone_with_git(repo_url: str, repo_dir: Path, branch: str):
    Repo = _import_repo()
    auth_url = repo_url
    gh_token = settings.github_token.get_secret_value()
    azdo_pat = settings.azdo_pat.get_secret_value()
    if gh_token and "github.com" in repo_url:
        auth_url = repo_url.replace("https://", f"https://{gh_token}@")
    elif azdo_pat and ("dev.azure.com" in repo_url or "visualstudio.com" in repo_url):
        auth_url = repo_url.replace("https://", f"https://:{azdo_pat}@")

    logger.info("Cloning %s into %s", repo_url, repo_dir)
    repo = Repo.clone_from(auth_url, str(repo_dir), branch=branch)
    return repo, repo_dir


def _clone_github_zipball(repo_url: str, repo_dir: Path, branch: str):
    owner, repo_name = parse_repo_url(repo_url)
    headers = _headers()
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        ref_resp = client.get(
            f"{GITHUB_API}/repos/{owner}/{repo_name}/git/ref/heads/{branch}",
            headers=headers,
        )
        if ref_resp.status_code == 404 and branch == "main":
            branch = "master"
            ref_resp = client.get(
                f"{GITHUB_API}/repos/{owner}/{repo_name}/git/ref/heads/{branch}",
                headers=headers,
            )
        ref_resp.raise_for_status()
        base_commit_sha = ref_resp.json()["object"]["sha"]

        commit_resp = client.get(
            f"{GITHUB_API}/repos/{owner}/{repo_name}/git/commits/{base_commit_sha}",
            headers=headers,
        )
        commit_resp.raise_for_status()
        base_tree_sha = commit_resp.json()["tree"]["sha"]

        zip_resp = client.get(
            f"{GITHUB_API}/repos/{owner}/{repo_name}/zipball/{branch}",
            headers=headers,
        )
        zip_resp.raise_for_status()

    repo_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
        root_prefix = zf.namelist()[0].split("/")[0]
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = Path(info.filename).relative_to(root_prefix)
            target = repo_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info.filename))

    ws = WorkspaceRepo(
        path=repo_dir,
        provider="github",
        owner=owner,
        repo_name=repo_name,
        base_branch=branch,
        base_commit_sha=base_commit_sha,
        base_tree_sha=base_tree_sha,
    )
    _save_workspace_meta(ws)
    logger.info(
        "Zipball clone ready for %s/%s @ %s (%s)",
        owner,
        repo_name,
        branch,
        base_commit_sha[:8],
    )
    return ws, repo_dir


def _azdo_headers() -> dict:
    import base64

    pat = settings.azdo_pat.get_secret_value()
    encoded = base64.b64encode(f":{pat}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


def _parse_azdo_url(repo_url: str) -> tuple[str, str, str]:
    """Return (org_url, project, repo_name) from an AzDO git URL."""
    # https://dev.azure.com/{org}/{project}/_git/{repo}
    url = repo_url.rstrip("/").removesuffix(".git")
    parts = url.split("/")
    if "_git" not in parts:
        raise RuntimeError(f"Unrecognized Azure DevOps repo URL: {repo_url}")
    gi = parts.index("_git")
    project = parts[gi - 1]
    repo_name = parts[gi + 1]
    # org url = https://dev.azure.com/{org}
    org_url = "/".join(parts[: parts.index(project)])
    if settings.azdo_org_url:
        org_url = settings.azdo_org_url.rstrip("/")
    return org_url, project, repo_name


def _hash_workspace_files(repo_dir: Path) -> dict[str, str]:
    import hashlib

    hashes: dict[str, str] = {}
    for path in repo_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_dir).as_posix()
        if rel == META_FILE or any(p.startswith(".") for p in Path(rel).parts):
            continue
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _clone_azdo_zip(repo_url: str, repo_dir: Path, branch: str):
    org_url, project, repo_name = _parse_azdo_url(repo_url)
    headers = _azdo_headers()

    with httpx.Client(timeout=180, follow_redirects=True) as client:
        repos_resp = client.get(
            f"{org_url}/{project}/_apis/git/repositories?api-version=7.1",
            headers=headers,
        )
        repos_resp.raise_for_status()
        repos = repos_resp.json().get("value") or []
        match = next((r for r in repos if r.get("name") == repo_name), None)
        if not match:
            raise RuntimeError(f"AzDO repo not found: {repo_name}")
        repo_id = match["id"]

        refs_resp = client.get(
            f"{org_url}/{project}/_apis/git/repositories/{repo_id}/refs"
            f"?filter=heads/{branch}&api-version=7.1",
            headers=headers,
        )
        refs_resp.raise_for_status()
        refs = refs_resp.json().get("value") or []
        if not refs and branch == "main":
            branch = "master"
            refs_resp = client.get(
                f"{org_url}/{project}/_apis/git/repositories/{repo_id}/refs"
                f"?filter=heads/{branch}&api-version=7.1",
                headers=headers,
            )
            refs_resp.raise_for_status()
            refs = refs_resp.json().get("value") or []
        if not refs:
            raise RuntimeError(f"AzDO branch not found: {branch}")
        base_commit_sha = refs[0]["objectId"]

        zip_resp = client.get(
            f"{org_url}/{project}/_apis/git/repositories/{repo_id}/items"
            f"?path=/&versionDescriptor.versionType=branch"
            f"&versionDescriptor.version={branch}"
            f"&$format=zip&api-version=7.1",
            headers=headers,
        )
        zip_resp.raise_for_status()

    repo_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # AzDO zip paths are usually repo-root relative already
            rel = info.filename
            if rel.startswith(repo_name + "/"):
                rel = rel[len(repo_name) + 1 :]
            target = repo_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info.filename))

    ws = WorkspaceRepo(
        path=repo_dir,
        provider="azure_devops",
        repo_name=repo_name,
        base_branch=branch,
        base_commit_sha=base_commit_sha,
        azdo_project=project,
        azdo_repo_id=repo_id,
        file_hashes=_hash_workspace_files(repo_dir),
    )
    _save_workspace_meta(ws)
    logger.info(
        "AzDO zip clone ready for %s/%s @ %s (%s)",
        project,
        repo_name,
        branch,
        base_commit_sha[:8],
    )
    return ws, repo_dir


def _save_workspace_meta(repo: WorkspaceRepo) -> None:
    data = {
        "provider": repo.provider,
        "owner": repo.owner,
        "repo_name": repo.repo_name,
        "base_branch": repo.base_branch,
        "branch_name": repo.branch_name,
        "base_commit_sha": repo.base_commit_sha,
        "base_tree_sha": repo.base_tree_sha,
        "azdo_project": repo.azdo_project,
        "azdo_repo_id": repo.azdo_repo_id,
        "branch_tip": repo.branch_tip,
        "file_hashes": repo.file_hashes,
    }
    (repo.path / META_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def open_workspace(workspace_path: str):
    """Re-open a cloned workspace (GitPython Repo or HTTP WorkspaceRepo)."""
    path = Path(workspace_path)
    meta_path = path / META_FILE
    if meta_path.exists():
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return WorkspaceRepo(path=path, **data)
    if _has_system_git():
        Repo = _import_repo()
        return Repo(str(path))
    raise RuntimeError(
        f"No git repo and no {META_FILE} at {workspace_path}. "
        "Re-run the coding task after deploy."
    )


def create_branch(repo, branch_name: str) -> None:
    if isinstance(repo, WorkspaceRepo):
        repo.branch_name = branch_name
        _save_workspace_meta(repo)
        logger.info("Prepared HTTP workspace branch %s", branch_name)
        return
    repo.git.checkout("-b", branch_name)
    logger.info("Created branch %s", branch_name)


def apply_changes(repo_dir: Path, file_changes: list[dict]) -> list[str]:
    modified_files = []
    for change in file_changes:
        file_path = repo_dir / change["path"]
        action = change.get("action", "modify")

        if action in ("modify", "create"):
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(change["content"])
            modified_files.append(change["path"])
            logger.info("Applied %s to %s", action, change["path"])
        elif action == "delete" and file_path.exists():
            os.remove(file_path)
            modified_files.append(change["path"])
            logger.info("Deleted %s", change["path"])

    return modified_files


def commit_and_push(repo, message: str, branch: str) -> str:
    if isinstance(repo, WorkspaceRepo):
        if repo.provider == "azure_devops":
            return _commit_and_push_azdo_api(repo, message, branch or repo.branch_name)
        return _commit_and_push_github_api(repo, message, branch or repo.branch_name)

    repo.git.add(A=True)

    if not repo.is_dirty() and not repo.untracked_files:
        logger.info("No changes to commit")
        return ""

    repo.index.commit(message)
    origin = repo.remotes.origin
    origin.push(branch)
    sha = repo.head.commit.hexsha
    logger.info("Pushed commit %s to %s", sha[:8], branch)
    return sha


def _commit_and_push_azdo_api(repo: WorkspaceRepo, message: str, branch: str) -> str:
    if not branch:
        raise RuntimeError("Missing branch name for Azure DevOps API push")
    if not repo.azdo_project or not repo.azdo_repo_id:
        raise RuntimeError("Missing AzDO project/repo id in workspace meta")

    import hashlib

    org_url = settings.azdo_org_url.rstrip("/")
    headers = _azdo_headers()
    old_hashes = repo.file_hashes or {}
    changes: list[dict] = []

    current_paths: set[str] = set()
    for path in sorted(repo.path.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo.path).as_posix()
        if rel == META_FILE or any(p.startswith(".") for p in Path(rel).parts):
            continue
        current_paths.add(rel)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if old_hashes.get(rel) == digest:
            continue
        try:
            text = content.decode("utf-8")
            content_type = "rawtext"
            payload = text
        except UnicodeDecodeError:
            import base64

            content_type = "base64encoded"
            payload = base64.b64encode(content).decode("ascii")
        change_type = "edit" if rel in old_hashes else "add"
        changes.append(
            {
                "changeType": change_type,
                "item": {"path": "/" + rel},
                "newContent": {"content": payload, "contentType": content_type},
            }
        )

    for rel in sorted(set(old_hashes) - current_paths):
        changes.append(
            {
                "changeType": "delete",
                "item": {"path": "/" + rel},
            }
        )

    if not changes:
        logger.info("No AzDO file changes to push")
        return ""

    # Ensure branch exists from main tip, then push commit on top.
    org_url = settings.azdo_org_url.rstrip("/")
    headers = _azdo_headers()
    ref_name = f"refs/heads/{branch}"
    with httpx.Client(timeout=120) as client:
        if not repo.branch_tip:
            refs_url = (
                f"{org_url}/{repo.azdo_project}/_apis/git/repositories/"
                f"{repo.azdo_repo_id}/refs?api-version=7.1"
            )
            create_ref = client.post(
                refs_url,
                headers=headers,
                json=[
                    {
                        "name": ref_name,
                        "oldObjectId": "0000000000000000000000000000000000000000",
                        "newObjectId": repo.base_commit_sha,
                    }
                ],
            )
            if create_ref.status_code >= 400:
                # Branch may already exist from a prior attempt
                logger.warning(
                    "AzDO create-ref returned %s — continuing push", create_ref.status_code
                )
            repo.branch_tip = repo.base_commit_sha

        old_object_id = repo.branch_tip or repo.base_commit_sha
        url = (
            f"{org_url}/{repo.azdo_project}/_apis/git/repositories/"
            f"{repo.azdo_repo_id}/pushes?api-version=7.1"
        )
        payload = {
            "refUpdates": [{"name": ref_name, "oldObjectId": old_object_id}],
            "commits": [{"comment": message, "changes": changes}],
        }
        resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            logger.error("AzDO push failed: %s %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        data = resp.json()

    commits = data.get("commits") or []
    sha = commits[0]["commitId"] if commits else ""
    repo.branch_tip = sha or repo.branch_tip
    repo.file_hashes = _hash_workspace_files(repo.path)
    _save_workspace_meta(repo)
    logger.info("Pushed AzDO commit %s to %s", (sha or "?")[:8], branch)
    return sha


def _commit_and_push_github_api(repo: WorkspaceRepo, message: str, branch: str) -> str:
    if not branch:
        raise RuntimeError("Missing branch name for GitHub API push")

    headers = _headers()
    owner, name = repo.owner, repo.repo_name
    tree_items: list[dict] = []

    for path in sorted(repo.path.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo.path).as_posix()
        if rel == META_FILE or any(p.startswith(".") for p in Path(rel).parts):
            continue
        content = path.read_bytes()
        with httpx.Client(timeout=60) as client:
            blob_resp = client.post(
                f"{GITHUB_API}/repos/{owner}/{name}/git/blobs",
                headers=headers,
                json={
                    "content": content.decode("utf-8", errors="surrogateescape"),
                    "encoding": "utf-8",
                },
            )
            # Binary-safe fallback
            if blob_resp.status_code >= 400:
                import base64

                blob_resp = client.post(
                    f"{GITHUB_API}/repos/{owner}/{name}/git/blobs",
                    headers=headers,
                    json={
                        "content": base64.b64encode(content).decode("ascii"),
                        "encoding": "base64",
                    },
                )
            blob_resp.raise_for_status()
            tree_items.append(
                {
                    "path": rel,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_resp.json()["sha"],
                }
            )

    if not tree_items:
        logger.info("No files to push via GitHub API")
        return ""

    with httpx.Client(timeout=60) as client:
        tree_resp = client.post(
            f"{GITHUB_API}/repos/{owner}/{name}/git/trees",
            headers=headers,
            json={"tree": tree_items},
        )
        tree_resp.raise_for_status()
        tree_sha = tree_resp.json()["sha"]

        commit_resp = client.post(
            f"{GITHUB_API}/repos/{owner}/{name}/git/commits",
            headers=headers,
            json={
                "message": message,
                "tree": tree_sha,
                "parents": [repo.base_commit_sha],
            },
        )
        commit_resp.raise_for_status()
        commit_sha = commit_resp.json()["sha"]

        ref = f"refs/heads/{branch}"
        ref_resp = client.post(
            f"{GITHUB_API}/repos/{owner}/{name}/git/refs",
            headers=headers,
            json={"ref": ref, "sha": commit_sha},
        )
        if ref_resp.status_code == 422:
            # Branch exists — update it
            ref_resp = client.patch(
                f"{GITHUB_API}/repos/{owner}/{name}/git/refs/heads/{branch}",
                headers=headers,
                json={"sha": commit_sha, "force": True},
            )
        ref_resp.raise_for_status()

    logger.info("Pushed commit %s to %s via GitHub API", commit_sha[:8], branch)
    return commit_sha


def get_repo_tree(repo_dir: Path, max_depth: int = 3) -> str:
    lines = []
    for root, dirs, files in os.walk(repo_dir):
        rel = Path(root).relative_to(repo_dir)
        depth = len(rel.parts)
        if depth > max_depth:
            dirs.clear()
            continue
        if any(p.startswith(".") for p in rel.parts):
            continue
        indent = "  " * depth
        if depth > 0:
            lines.append(f"{indent}{rel.name}/")
        for f in sorted(files):
            if not f.startswith("."):
                lines.append(f"{indent}  {f}")
    return "\n".join(lines[:200])


def cleanup_workspace(task_id: str) -> None:
    workspace = get_workspace_path(task_id)
    if workspace.exists():
        shutil.rmtree(workspace)
        logger.info("Cleaned up workspace for task %s", task_id)
