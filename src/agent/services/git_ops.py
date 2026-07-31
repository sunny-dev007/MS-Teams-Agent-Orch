import os
import shutil
from pathlib import Path

from git import Repo

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)


def get_workspace_path(task_id: str) -> Path:
    path = Path(settings.workspace_dir) / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def clone_repo(repo_url: str, task_id: str, branch: str = "main") -> tuple[Repo, Path]:
    workspace = get_workspace_path(task_id)
    repo_dir = workspace / "repo"

    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    token = settings.github_token.get_secret_value()
    if token and "github.com" in repo_url:
        auth_url = repo_url.replace("https://", f"https://{token}@")
    else:
        auth_url = repo_url

    logger.info("Cloning %s into %s", repo_url, repo_dir)
    repo = Repo.clone_from(auth_url, str(repo_dir), branch=branch)
    return repo, repo_dir


def create_branch(repo: Repo, branch_name: str) -> None:
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


def commit_and_push(repo: Repo, message: str, branch: str) -> str:
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
