"""Test-fixer agent — repairs Azure Pipelines pytest failures after Sunny approves."""

from __future__ import annotations

import json
import re
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
from agent.services.llm import invoke_llm, user_facing_llm_error

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "test_fixer_system.txt").read_text()


async def fix_ci_test_failures(state: AgentState) -> AgentState:
    """Clone repo, apply focused test/code fixes from CI logs, push branch."""
    task_id = state.get("task_id", "unknown")
    repo_url = state.get("repo_url", "")
    summary = (state.get("ci_failure_summary") or "").strip()
    build_id = state.get("ci_build_id") or ""
    phone = state.get("whatsapp_phone") or ""

    if not repo_url:
        return {
            **state,
            "status": "failed",
            "error": "No repository URL for CI fix",
            "notification_text": "Cannot fix CI — repository URL missing. Reply *stop* or *check my repos*.",
        }

    project = state.get("azdo_project") or ""
    if build_id and project and not summary:
        try:
            from agent.services.azure_devops import get_build_failure_summary

            summary = await get_build_failure_summary(project, build_id)
        except Exception:
            logger.exception("Failed loading CI logs for task %s", task_id)

    if not summary:
        summary = (
            "Pipeline Run tests failed. Inspect pytest failures under tests/ "
            "and FastAPI routes under src/agent/api/ (404s often mean missing routers)."
        )

    branch_name = state.get("branch_name") or f"agent/{task_id}"
    # Prefer repairing the same feature branch when still open; else hotfix branch.
    phase = (state.get("ci_watch_phase") or "").lower()
    if phase == "post_merge":
        branch_name = f"agent/{task_id}-ci-fix"

    try:
        repo, repo_dir = clone_repo(repo_url, f"{task_id}-ci-fix")
        create_branch(repo, branch_name)

        tree = get_repo_tree(repo_dir)
        relevant = _read_failure_related_files(repo_dir, summary)

        prompt = (
            f"## Task ID\n{task_id}\n\n"
            f"## Original user request\n{state.get('user_message') or state.get('email_body') or 'N/A'}\n\n"
            f"## CI / pytest failure logs\n{summary[:12000]}\n\n"
            f"## Repository tree\n{tree[:4000]}\n\n"
            f"## Relevant file contents\n{relevant}\n\n"
            "Return a JSON array of file changes to make the tests pass. "
            "Prefer fixing production code or test setup; do not delete failing tests "
            "unless they are clearly obsolete."
        )

        response = await invoke_llm(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ],
            purpose="coding",
        )
        changes = _parse_changes(response.content if hasattr(response, "content") else str(response))
        if not changes:
            return {
                **state,
                "status": "failed",
                "error": "test_fixer produced no changes",
                "notification_text": (
                    f"*Sunny's AI Agent* — Test fixer could not produce a fix (`{task_id}`).\n\n"
                    f"Pipeline: {state.get('pipeline_url') or 'N/A'}\n"
                    "Reply *FIX TESTS* to retry, or *SKIP* / *STOP*."
                ),
                "ci_failure_summary": summary,
            }

        apply_changes(repo_dir, changes)
        from agent.services.git_ops import commit_and_push, open_workspace

        workspace = open_workspace(str(repo_dir))
        sha = commit_and_push(
            workspace,
            f"agent({task_id}): fix CI test failures",
            branch_name,
        )
        if not sha:
            return {
                **state,
                "status": "failed",
                "notification_text": (
                    f"Test fixer applied no net git changes for `{task_id}`. "
                    "Reply *FIX TESTS* to retry or *STOP*."
                ),
            }

        pr_url = state.get("pr_url") or ""
        # Open/refresh PR when fixing after a broken main merge
        if phase == "post_merge" or not pr_url:
            pr_url = await _ensure_fix_pr(state, task_id, branch_name, summary, changes) or pr_url

        out = {
            **state,
            "status": "ci_fix_pushed",
            "branch_name": branch_name,
            "commit_sha": sha,
            "file_changes": changes,
            "workspace_path": str(repo_dir),
            "pr_url": pr_url,
            "ci_failure_summary": summary,
            "pipeline_status": "watching",
            "notification_text": (
                f"*Sunny's AI Agent* — Test fixer pushed a repair (`{task_id}`)\n\n"
                f"Branch: `{branch_name}`\n"
                f"Changes:\n"
                + "\n".join(
                    f"- {c.get('action', 'modify')} `{c.get('path')}`" for c in changes[:12]
                )
                + (f"\n\nPR: {pr_url}" if pr_url else "")
                + "\n\nWatching Azure Pipelines again. I'll message you when CI finishes."
            ),
        }

        await _restart_ci_watch(out, phone)
        if phone:
            from agent.workflow.gates import persist_pipeline_watching_gate

            await persist_pipeline_watching_gate(phone, out)
        return out
    except Exception as exc:
        logger.exception("test_fixer failed for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "error": str(exc),
            "notification_text": (
                f"*Sunny's AI Agent* — Test fixer error (`{task_id}`)\n\n"
                f"{user_facing_llm_error(exc)}\n\n"
                "Reply *FIX TESTS* to retry, *SKIP*, or *STOP*."
            ),
            "ci_failure_summary": summary,
        }


def _read_failure_related_files(repo_dir: Path, summary: str) -> str:
    """Pull likely test/source files mentioned in the failure log."""
    paths: list[str] = []
    for match in re.finditer(
        r"(?:tests|src)/[\w./-]+\.py", summary, flags=re.I
    ):
        paths.append(match.group(0).replace("\\", "/"))
    # Always include main + webhook tests when 404s show up
    if "404" in summary or "not found" in summary.lower():
        paths.extend(
            [
                "src/agent/main.py",
                "tests/conftest.py",
                "tests/test_api/test_whatsapp_webhook.py",
                "tests/test_api/test_whatsapp_gate_routing.py",
            ]
        )
    paths.extend(
        [
            "src/agent/main.py",
            "src/agent/api/whatsapp.py",
            "tests/conftest.py",
        ]
    )
    seen: set[str] = set()
    chunks: list[str] = []
    for rel in paths:
        rel = rel.lstrip("./")
        if rel in seen:
            continue
        seen.add(rel)
        path = repo_dir / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")[:6000]
        except Exception:
            continue
        chunks.append(f"### {rel}\n```\n{text}\n```")
        if len(chunks) >= 10:
            break
    return "\n\n".join(chunks) if chunks else "(no local files matched)"


def _parse_changes(raw: str) -> list[dict]:
    text = (raw or "").strip()
    if "```" in text:
        text = re.sub(r"^.*?```(?:json)?\s*", "", text, flags=re.S)
        text = re.sub(r"\s*```.*$", "", text, flags=re.S)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end < 0:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        out.append(
            {
                "path": item["path"],
                "action": item.get("action") or "modify",
                "content": item.get("content") or "",
            }
        )
    return out


async def _ensure_fix_pr(
    state: AgentState,
    task_id: str,
    branch_name: str,
    summary: str,
    changes: list[dict],
) -> str:
    provider = (state.get("repo_provider") or "").lower()
    title = f"[AI Agent] CI test fix for {task_id}"
    body = (
        f"Automated CI test repair for task `{task_id}`.\n\n"
        f"### Failure excerpt\n```\n{summary[:2500]}\n```\n\n"
        "### Files\n"
        + "\n".join(f"- `{c.get('path')}`" for c in changes)
    )
    try:
        if provider in ("azure_devops", "azdo", "ado") or "dev.azure.com" in (
            state.get("repo_url") or ""
        ):
            from agent.config import settings
            from agent.services import azure_devops as azdo

            project = state.get("azdo_project") or ""
            repo_id = state.get("azdo_repo_id") or ""
            repo_name = state.get("repo_name") or ""
            if not project or not repo_id:
                return ""
            # Reuse open PR on this branch if present
            existing = await azdo.find_active_pr_for_branch(project, repo_id, branch_name)
            if existing:
                pr_id = existing.get("pullRequestId")
                org = settings.azdo_org_url.rstrip("/")
                return f"{org}/{project}/_git/{repo_name or repo_id}/pullrequest/{pr_id}"
            pr = await azdo.create_pull_request(
                project,
                repo_id,
                title,
                body,
                branch_name,
            )
            pr_id = pr.get("pullRequestId")
            org = settings.azdo_org_url.rstrip("/")
            return f"{org}/{project}/_git/{repo_name or repo_id}/pullrequest/{pr_id}"

        from agent.services.github import create_pull_request

        owner = state.get("repo_owner") or ""
        repo_name = state.get("repo_name") or ""
        if not owner or not repo_name:
            return ""
        pr = await create_pull_request(
            owner=owner,
            repo=repo_name,
            title=title,
            body=body,
            head=branch_name,
            base="main",
        )
        return pr.get("html_url") or ""
    except Exception:
        logger.exception("Could not open CI-fix PR for task %s", task_id)
        return ""


async def _restart_ci_watch(state: AgentState, phone: str) -> None:
    if not phone:
        return
    project = state.get("azdo_project") or ""
    repo_name = state.get("repo_name") or ""
    if not project:
        return
    try:
        from agent.config import settings
        from agent.services.ci_watch import save_ci_watch, start_azdo_ci_watch
        from agent.services import azure_devops as azdo

        pipeline = await azdo.find_pipeline_for_repo(project, repo_name)
        pipeline_url = ""
        if pipeline:
            org = settings.azdo_org_url.rstrip("/")
            pipeline_url = (
                f"{org}/{project}/_build?definitionId={pipeline.get('id')}"
            )
        await save_ci_watch(
            task_id=state.get("task_id") or "",
            phone=phone,
            project=project,
            repo_name=repo_name,
            repo_id=str(state.get("azdo_repo_id") or ""),
            pr_url=state.get("pr_url") or "",
            pipeline_url=pipeline_url,
            commit_sha=state.get("commit_sha") or "",
            live_url=f"{settings.agent_app_url.rstrip('/')}/portal",
            notes="ci_fix_validation",
        )
        start_azdo_ci_watch(state.get("task_id") or "")
    except Exception:
        logger.exception("Failed restarting CI watch after test fix")
