"""Recover PR / gate context after App Service recycle loses SQLite visibility."""

from __future__ import annotations

from typing import Any

from agent.core.logging import get_logger
from agent.core.gate_store import read_gate, write_gate
from agent.workflow.gates import GATE_PLAN, GATE_PR_MODE, session_has_pr

logger = get_logger(__name__)


async def recover_session_for_gates(session: dict[str, Any]) -> dict[str, Any]:
    """Fill missing PR fields / advance stale plan_approval using durable sources.

    Order: durable gate file (already merged in get_session) → Task row →
    LangGraph checkpoint → Azure DevOps open PR on agent/{task_id}.
    """
    out = dict(session or {})
    data = dict(out.get("data") or {})
    phone = out.get("phone") or ""
    task_id = str(data.get("pending_task_id") or "").strip()

    # Durable file may still have richer data if merge ran on empty SQL row
    gate = read_gate(phone) if phone else None
    if gate:
        gate_data = dict(gate.get("data") or {})
        for key, val in gate_data.items():
            if val not in (None, "", [], {}) and not data.get(key):
                data[key] = val
        if gate.get("task_id") and not task_id:
            task_id = str(gate["task_id"])
            data["pending_task_id"] = task_id
        if gate.get("awaiting") in (GATE_PR_MODE, "manual_pr_review", "approval"):
            if out.get("awaiting") == GATE_PLAN or not out.get("awaiting"):
                out["awaiting"] = gate["awaiting"]
        if gate.get("provider") and not out.get("provider"):
            out["provider"] = gate["provider"]

    if not task_id:
        out["data"] = data
        return out

    if not session_has_pr(data):
        enriched = await _pr_from_task_row(task_id)
        if enriched:
            data.update(enriched)
            logger.info("Recovered PR context from Task row task=%s", task_id)

    if not session_has_pr(data):
        enriched = await _pr_from_checkpoint(task_id)
        if enriched:
            for key, val in enriched.items():
                if val not in (None, "", [], {}) and not data.get(key):
                    data[key] = val
            logger.info("Recovered PR context from checkpoint task=%s", task_id)

    if not session_has_pr(data):
        enriched = await _pr_from_azdo(data, task_id)
        if enriched:
            data.update(enriched)
            logger.info("Recovered PR context from Azure DevOps task=%s", task_id)

    if session_has_pr(data) and out.get("awaiting") in (GATE_PLAN, None, ""):
        out["awaiting"] = GATE_PR_MODE
        logger.warning(
            "Recovered stale plan_approval → pr_review_mode phone=%s task=%s pr=%s",
            phone,
            task_id,
            data.get("pr_url"),
        )
        if phone:
            try:
                write_gate(
                    phone,
                    awaiting=GATE_PR_MODE,
                    provider=out.get("provider") or data.get("repo_provider") or "",
                    data=data,
                )
            except Exception:
                logger.exception("Failed rewriting durable gate after recovery")

    out["data"] = data
    return out


async def _pr_from_task_row(task_id: str) -> dict[str, Any]:
    try:
        from agent.models.db import async_session
        from agent.models.task import Task

        async with async_session() as db:
            row = await db.get(Task, task_id)
            if not row or not row.pr_url:
                return {}
            out: dict[str, Any] = {"pr_url": row.pr_url}
            if row.branch_name:
                out["branch_name"] = row.branch_name
            if row.repo_url:
                out["repo_url"] = row.repo_url
            return out
    except Exception:
        logger.exception("Task row PR lookup failed for %s", task_id)
        return {}


async def _pr_from_checkpoint(task_id: str) -> dict[str, Any]:
    try:
        from agent.agents import graph as graph_mod

        compiled = await graph_mod._get_compiled()
        snap = await compiled.aget_state({"configurable": {"thread_id": task_id}})
        values = dict(snap.values or {})
        keys = (
            "pr_url",
            "pr_id",
            "pr_number",
            "branch_name",
            "repo_url",
            "repo_name",
            "repo_provider",
            "azdo_project",
            "azdo_repo_id",
            "workspace_path",
            "user_message",
            "file_changes",
            "implementation_plan",
        )
        return {k: values[k] for k in keys if values.get(k) not in (None, "", [], {})}
    except Exception:
        logger.warning("Checkpoint PR lookup failed for task %s", task_id)
        return {}


async def _pr_from_azdo(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    provider = (data.get("repo_provider") or "").lower()
    if provider and provider not in ("azure_devops", "azdo", "ado"):
        return {}
    project = data.get("azdo_project") or ""
    repo_id = data.get("azdo_repo_id") or ""
    if not project or not repo_id:
        return {}
    branch = data.get("branch_name") or f"agent/{task_id}"
    try:
        from agent.services.azure_devops import find_active_pr_for_branch

        pr = await find_active_pr_for_branch(project, repo_id, branch)
        if not pr:
            return {}
        pr_id = pr.get("pullRequestId")
        web = (
            (pr.get("_links") or {}).get("web", {}).get("href")
            or data.get("pr_url")
            or ""
        )
        if not web and pr_id:
            # Best-effort URL when API omits web link
            repo_name = data.get("repo_name") or repo_id
            web = (
                f"https://dev.azure.com/{project}/_git/{repo_name}/pullrequest/{pr_id}"
            )
        return {
            "pr_id": pr_id,
            "pr_number": pr_id,
            "pr_url": web,
            "branch_name": branch,
        }
    except Exception:
        logger.exception("AzDO PR lookup failed for task %s", task_id)
        return {}
