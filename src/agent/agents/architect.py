"""Pre-development architect — detailed plan for Sunny's approval."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.developer import _read_relevant_files
from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.git_ops import clone_repo, get_repo_tree
from agent.services.github import parse_repo_url
from agent.services.llm import invoke_llm, user_facing_llm_error

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "architect_system.txt").read_text()
PLAN_TEMPLATE = (PROMPTS_DIR / "architect_plan.txt").read_text()


async def plan_implementation(state: AgentState) -> AgentState:
    task_id = state.get("task_id", "unknown")
    repo_url = state.get("repo_url", "")
    user_msg = state.get("user_message", "") or state.get("email_body", "")

    if not repo_url:
        return {
            **state,
            "status": "failed",
            "error": "No repository URL",
            "notification_text": "No repository selected for planning.",
        }

    owner, repo_name = "", ""
    provider = state.get("repo_provider") or (
        "azure_devops"
        if "dev.azure.com" in repo_url or "visualstudio.com" in repo_url
        else "github"
    )
    if provider == "azure_devops":
        try:
            from agent.services.git_ops import _parse_azdo_url

            _, project, repo_name = _parse_azdo_url(repo_url)
            owner = project
        except Exception:
            owner, repo_name = parse_repo_url(repo_url)
    else:
        owner, repo_name = parse_repo_url(repo_url)

    repo_dir = None
    tree = ""
    try:
        _, repo_dir = clone_repo(repo_url, task_id)
        tree = get_repo_tree(repo_dir)
        relevant = _read_relevant_files(repo_dir, max_files=12, max_size=4000)

        prompt = PLAN_TEMPLATE.format(
            task_description=user_msg,
            repo_url=repo_url,
            repo_tree=tree,
            file_contents=relevant,
        )

        response = await invoke_llm(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ],
            temperature=0.2,
            role="planning",
        )
        plan = _parse_plan(response.content)
    except Exception as e:
        logger.exception("Architect LLM unavailable for task %s — using heuristic plan", task_id)
        plan = _heuristic_plan(user_msg, tree, repo_url)
        if not plan.get("summary"):
            return {
                **state,
                "status": "failed",
                "error": str(e),
                "notification_text": user_facing_llm_error("implementation plan"),
            }

    detail = plan.get("whatsapp_detail") or _format_plan_fallback(plan)

    logger.info(
        "Architect plan ready for task %s complexity=%s heuristic=%s",
        task_id,
        plan.get("estimated_complexity"),
        plan.get("_heuristic", False),
    )

    meta: dict = {
        "repo_owner": owner,
        "repo_name": repo_name,
        "repo_provider": provider,
        "workspace_path": str(repo_dir) if repo_dir else "",
        "branch_name": f"agent/{task_id}",
    }
    if provider == "azure_devops":
        meta["azdo_project"] = state.get("azdo_project") or owner
        if state.get("azdo_repo_id"):
            meta["azdo_repo_id"] = state.get("azdo_repo_id")

    return {
        **state,
        "status": "awaiting_plan_approval",
        "implementation_plan": json.dumps(plan, ensure_ascii=False),
        "plan_summary": plan.get("summary", ""),
        "notification_text": detail,
        **meta,
    }


def _heuristic_plan(user_msg: str, repo_tree: str, repo_url: str) -> dict:
    """Structured draft plan when all LLM deployments are temporarily unavailable."""
    msg = user_msg.lower()
    files = _guess_target_files(msg, repo_tree)

    approach_parts = [f"Address Sunny's request: {user_msg.strip()}"]
    if any(k in msg for k in ("dark", "dark mode", "dark layout", "dark theme")):
        approach_parts.append(
            "Apply a dark theme (dark background, light text, accessible contrast, modern spacing)."
        )
    if "portal" in msg or "page" in msg:
        approach_parts.append("Update the portal / landing page layout and styling.")
    if "version" in msg or "deployed" in msg:
        approach_parts.append(
            "Show the deployed app version on the page (from env, package metadata, or build stamp)."
        )
    if "redesign" in msg or "ui" in msg or "layout" in msg:
        approach_parts.append("Refresh UI structure while keeping existing functionality intact.")

    plan = {
        "summary": user_msg.strip()[:240] or "Implement requested change",
        "approach": " ".join(approach_parts),
        "files_to_modify": files,
        "files_to_create": [],
        "dependencies": [],
        "estimated_complexity": "medium",
        "testing_plan": "Verify the updated page in a browser after deploy; confirm version label if requested.",
        "risks": [
            {
                "severity": "low",
                "description": "Draft plan — review steps before development proceeds.",
            }
        ],
        "_heuristic": True,
    }
    plan["whatsapp_detail"] = _format_plan_fallback(plan)
    return plan


def _guess_target_files(user_msg: str, repo_tree: str) -> list[str]:
    msg = user_msg.lower()
    tree_lower = (repo_tree or "").lower()
    candidates: list[str] = []

    for line in (repo_tree or "").splitlines():
        path = line.strip().lstrip("- ").strip()
        if not path:
            continue
        lower = path.lower()
        if "portal" in msg and "portal" in lower and lower.endswith((".html", ".htm", ".css", ".tsx", ".jsx")):
            candidates.append(path)
        elif lower.endswith(("portal.html", "index.html")) and "portal" in msg:
            candidates.append(path)

    if not candidates and "portal" in msg:
        if "src/agent/web/portal.html" in tree_lower or "agent/web/portal.html" in tree_lower:
            candidates.append("src/agent/web/portal.html")
        else:
            candidates.append("src/agent/web/portal.html")

    if not candidates:
        candidates.append("relevant UI / template files for this repo")

    return candidates[:8]


def _parse_plan(content: str) -> dict:
    start = content.find("{")
    end = content.rfind("}") + 1
    if start != -1 and end > 0:
        try:
            return json.loads(content[start:end])
        except json.JSONDecodeError:
            pass
    return {
        "summary": "Could not parse structured plan",
        "whatsapp_detail": (content or "")[:3500],
        "estimated_complexity": "medium",
    }


def _format_plan_fallback(plan: dict) -> str:
    parts = [
        f"*Summary:* {plan.get('summary', 'N/A')}",
        f"*Complexity:* {plan.get('estimated_complexity', 'medium')}",
    ]
    if plan.get("approach"):
        parts.append(f"*Approach:*\n{plan['approach']}")
    files = (plan.get("files_to_modify") or []) + (plan.get("files_to_create") or [])
    if files:
        parts.append("*Files:*\n" + "\n".join(f"  - `{f}`" for f in files[:20]))
    if plan.get("dependencies"):
        parts.append("*Dependencies:*\n" + "\n".join(f"  - {d}" for d in plan["dependencies"][:10]))
    if plan.get("risks"):
        parts.append("*Risks:*")
        for r in plan["risks"][:6]:
            if isinstance(r, dict):
                parts.append(f"  - [{r.get('severity', '?')}] {r.get('description', '')}")
    if plan.get("testing_plan"):
        parts.append(f"*Testing:* {plan['testing_plan']}")
    return "\n\n".join(parts)[:3500]
