"""Pre-development architect — detailed plan for Sunny's approval."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.developer import _read_relevant_files
from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.services.git_ops import clone_repo, get_repo_tree
from agent.services.github import parse_repo_url
from agent.services.llm import get_llm

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

        llm = get_llm(temperature=0.2, role="planning")
        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])
        plan = _parse_plan(response.content)
        detail = plan.get("whatsapp_detail") or _format_plan_fallback(plan)

        logger.info("Architect plan ready for task %s complexity=%s", task_id, plan.get("estimated_complexity"))

        meta: dict = {
            "repo_owner": owner,
            "repo_name": repo_name,
            "repo_provider": provider,
            "workspace_path": str(repo_dir),
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
    except Exception as e:
        logger.exception("Architect planning failed for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "error": str(e),
            "notification_text": f"Planning failed: {e}",
        }


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
