import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.git_ops import (
    apply_changes,
    clone_repo,
    create_branch,
    get_repo_tree,
)
from agent.services.github import parse_repo_url
from agent.services.llm import get_llm

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "developer_system.txt").read_text()
CODEGEN_TEMPLATE = (PROMPTS_DIR / "developer_codegen.txt").read_text()


async def develop_code(state: AgentState) -> AgentState:
    task_id = state.get("task_id", "unknown")
    repo_url = state.get("repo_url", "")
    user_msg = state.get("user_message", "") or state.get("email_body", "")
    review_feedback = state.get("review_comments", "None")
    iteration = state.get("review_iteration", 0)

    if not repo_url:
        return {
            **state,
            "status": "failed",
            "error": "No repository URL provided",
            "notification_text": "No repository URL was provided. Please specify a repo.",
        }

    owner, repo_name = "", ""
    if "dev.azure.com" in repo_url or "visualstudio.com" in repo_url:
        try:
            from agent.services.git_ops import _parse_azdo_url

            _, project, repo_name = _parse_azdo_url(repo_url)
            owner = project
        except Exception:
            owner, repo_name = parse_repo_url(repo_url)
    else:
        owner, repo_name = parse_repo_url(repo_url)
    branch_name = f"agent/{task_id}"
    provider = state.get("repo_provider") or (
        "azure_devops"
        if "dev.azure.com" in repo_url or "visualstudio.com" in repo_url
        else "github"
    )

    try:
        repo, repo_dir = clone_repo(repo_url, task_id)

        if iteration == 0:
            create_branch(repo, branch_name)
        else:
            # GitPython checkout, or no-op for HTTP workspace (files already on disk)
            if hasattr(repo, "git"):
                repo.git.checkout(branch_name)
            else:
                create_branch(repo, branch_name)

        tree = get_repo_tree(repo_dir)
        relevant_files = _read_relevant_files(repo_dir)

        prompt = CODEGEN_TEMPLATE.format(
            task_description=user_msg,
            implementation_plan=state.get("implementation_plan") or state.get("plan_summary") or "None — use best judgment.",
            repo_tree=tree,
            file_contents=relevant_files,
            review_feedback=review_feedback,
        )

        llm = get_llm(temperature=0.1, role="default")
        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])

        file_changes = _parse_changes(response.content)

        if not file_changes:
            return {
                **state,
                "status": "failed",
                "error": "Developer agent produced no changes",
                "notification_text": "Could not generate any code changes for this task.",
            }

        apply_changes(repo_dir, file_changes)

        changes_summary = "\n".join(
            f"- {c['action']} `{c['path']}`" for c in file_changes
        )

        logger.info(
            "Developer produced %d file changes for task %s (iteration %d)",
            len(file_changes),
            task_id,
            iteration,
        )

        return {
            **state,
            "status": "development_complete",
            "file_changes": file_changes,
            "dev_reasoning": response.content[:500],
            "branch_name": branch_name,
            "repo_owner": owner,
            "repo_name": repo_name,
            "repo_provider": provider,
            "workspace_path": str(repo_dir),
            "notification_text": changes_summary,
            **(
                {
                    "azdo_project": getattr(repo, "azdo_project", "") or state.get("azdo_project", ""),
                    "azdo_repo_id": getattr(repo, "azdo_repo_id", "") or state.get("azdo_repo_id", ""),
                }
                if provider == "azure_devops"
                else {}
            ),
        }

    except Exception as e:
        logger.exception("Developer agent failed for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "error": str(e),
            "notification_text": f"Development failed: {e}",
        }


def _read_relevant_files(repo_dir: Path, max_files: int = 10, max_size: int = 5000) -> str:
    sections = []
    extensions = {".py", ".js", ".ts", ".java", ".go", ".yaml", ".yml", ".json", ".md"}
    count = 0

    for f in sorted(repo_dir.rglob("*")):
        if count >= max_files:
            break
        if not f.is_file() or f.suffix not in extensions:
            continue
        rel = f.relative_to(repo_dir)
        if any(p.startswith(".") for p in rel.parts) or "node_modules" in rel.parts:
            continue
        try:
            content = f.read_text()[:max_size]
            sections.append(f"### {rel}\n```\n{content}\n```")
            count += 1
        except (UnicodeDecodeError, PermissionError):
            continue

    return "\n\n".join(sections) if sections else "No readable files found."


def _parse_changes(content: str) -> list[dict]:
    start = content.find("[")
    end = content.rfind("]") + 1
    if start == -1 or end == 0:
        return []
    try:
        return json.loads(content[start:end])
    except json.JSONDecodeError:
        logger.warning("Failed to parse developer output as JSON")
        return []
