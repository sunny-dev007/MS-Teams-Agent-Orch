"""Durable Azure DevOps pipeline watches that survive App Service restarts.

Why: AzDO deploys for web.Whatsapp-AI-Agent restart this same App Service, which
kills the in-process wait inside coding_deployer — so WhatsApp never got a Final
evaluation. GitHub sample deploys target a different app and stay unchanged.
"""

from __future__ import annotations

import asyncio
import datetime
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.config import settings
from agent.core.logging import get_logger
from agent.models.db import Base, async_session, ensure_db_schema
from agent.services.ci_gate import check_ci_busy_for_context, wait_for_ci_idle

logger = get_logger(__name__)

# Avoid duplicate background watchers for the same task in one process.
_running_watches: set[str] = set()


class PendingCiWatch(Base):
    __tablename__ = "pending_ci_watches"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="azure_devops")
    project: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    repo_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    repo_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    pr_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    pipeline_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    live_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


@dataclass(frozen=True)
class CiTerminalResult:
    """Terminal CI outcome for WhatsApp Final evaluation."""

    outcome: str  # succeeded|failed|canceled|partiallySucceeded|timeout|no_ci|unknown
    provider: str = "azure_devops"
    label: str = ""
    url: str = ""
    detail: str = ""
    build_id: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome in ("succeeded", "partiallySucceeded")


def _normalize_outcome(raw: str | None) -> str:
    val = (raw or "").strip().lower().replace(" ", "")
    aliases = {
        "succeeded": "succeeded",
        "failed": "failed",
        "canceled": "canceled",
        "cancelled": "canceled",
        "partiallysucceeded": "partiallySucceeded",
        "timeout": "timeout",
        "no_ci": "no_ci",
        "nociobserved": "no_ci",
    }
    return aliases.get(val, val or "unknown")


async def save_ci_watch(
    *,
    task_id: str,
    phone: str,
    project: str,
    repo_name: str,
    repo_id: str = "",
    pr_url: str = "",
    pipeline_url: str = "",
    commit_sha: str = "",
    live_url: str = "",
    notes: str = "",
) -> None:
    await ensure_db_schema()
    now = time.time()
    async with async_session() as db:
        row = await db.get(PendingCiWatch, task_id)
        if row is None:
            row = PendingCiWatch(
                task_id=task_id,
                phone=phone,
                provider="azure_devops",
                created_at=now,
            )
            db.add(row)
        row.phone = phone
        row.provider = "azure_devops"
        row.project = project or ""
        row.repo_name = repo_name or ""
        row.repo_id = str(repo_id or "")
        row.pr_url = pr_url or ""
        row.pipeline_url = pipeline_url or ""
        row.commit_sha = commit_sha or ""
        row.live_url = live_url or ""
        row.notes = notes or None
        row.notified = False
        row.created_at = row.created_at or now
        await db.commit()
    logger.info("Saved durable AzDO CI watch for task %s", task_id)


async def mark_ci_watch_notified(task_id: str) -> None:
    async with async_session() as db:
        row = await db.get(PendingCiWatch, task_id)
        if not row:
            return
        row.notified = True
        await db.commit()


async def clear_ci_watch(task_id: str) -> None:
    async with async_session() as db:
        row = await db.get(PendingCiWatch, task_id)
        if row:
            await db.delete(row)
            await db.commit()


async def list_open_ci_watches(*, max_age_sec: int = 3 * 3600) -> list[dict[str, Any]]:
    await ensure_db_schema()
    cutoff = time.time() - max_age_sec
    async with async_session() as db:
        rows = (
            await db.execute(
                select(PendingCiWatch).where(
                    PendingCiWatch.notified.is_(False),
                    PendingCiWatch.created_at >= cutoff,
                    PendingCiWatch.provider == "azure_devops",
                )
            )
        ).scalars().all()
        return [
            {
                "task_id": r.task_id,
                "phone": r.phone,
                "provider": r.provider,
                "project": r.project,
                "repo_name": r.repo_name,
                "repo_id": r.repo_id,
                "pr_url": r.pr_url,
                "pipeline_url": r.pipeline_url,
                "commit_sha": r.commit_sha,
                "live_url": r.live_url,
                "notes": r.notes or "",
                "created_at": r.created_at,
            }
            for r in rows
        ]


async def get_latest_azdo_build_result(
    project: str,
    *,
    repo_id: str = "",
    repo_name: str = "",
    commit_sha: str = "",
    min_created_at: float | None = None,
    require_sha_match: bool = False,
) -> CiTerminalResult | None:
    """Return the newest completed AzDO build for this repo (optional SHA match)."""
    from agent.services import azure_devops as azdo

    if not project:
        return None
    try:
        builds = await azdo.list_builds(
            project,
            status_filter="2",  # completed
            top=15,
            repository_id=repo_id or None,
        )
    except Exception:
        logger.exception("Failed listing completed AzDO builds for %s", project)
        return None

    wanted_repo = (repo_name or "").strip().lower()
    wanted_sha = (commit_sha or "").strip().lower()
    org = settings.azdo_org_url.rstrip("/")

    def _build_finished_at(build: dict) -> float | None:
        ts = build.get("finishTime") or build.get("startTime") or build.get("queueTime")
        if not ts:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return parsed.timestamp()
        except Exception:
            return None

    def _sha_matches(build: dict) -> bool:
        if not wanted_sha:
            return True
        src = (build.get("sourceVersion") or "").lower()
        if not src:
            return False
        return src.startswith(wanted_sha) or wanted_sha.startswith(src[:12])

    candidates: list[dict] = []
    for b in builds:
        repo_ref = ((b.get("repository") or {}).get("name") or "").lower()
        rid = str(((b.get("repository") or {}).get("id") or ""))
        if wanted_repo and repo_ref and repo_ref != wanted_repo:
            continue
        if repo_id and rid and rid != str(repo_id):
            continue
        if min_created_at is not None:
            finished = _build_finished_at(b)
            if finished is not None and finished + 2 < min_created_at:
                continue
        if wanted_sha and not _sha_matches(b):
            if require_sha_match:
                continue
            b = {**b, "_sha_mismatch": True}
        candidates.append(b)

    if not candidates:
        return None

    if require_sha_match and wanted_sha:
        exact = [c for c in candidates if not c.get("_sha_mismatch")]
        if not exact:
            return None
        build = exact[0]
    else:
        exact = [c for c in candidates if not c.get("_sha_mismatch")]
        build = (exact or candidates)[0]

    build_id = str(build.get("id") or "")
    outcome = _normalize_outcome(build.get("result"))
    if not outcome or outcome == "unknown":
        return None
    defn = (build.get("definition") or {}).get("name") or "pipeline"
    url = (
        f"{org}/{project}/_build/results?buildId={build_id}"
        if build_id
        else f"{org}/{project}/_build"
    )
    return CiTerminalResult(
        outcome=outcome,
        provider="Azure Pipelines",
        label=f"{project}/{repo_name}" if repo_name else project,
        url=url,
        detail=f"Build *{defn}* #{build_id} finished with `{outcome}`.",
        build_id=build_id,
    )


async def wait_for_azdo_terminal(
    ctx: dict[str, str],
    *,
    commit_sha: str = "",
    created_at: float | None = None,
    timeout_sec: int = 1500,
    poll_sec: int = 20,
) -> CiTerminalResult:
    """Wait until AzDO CI finishes and return the real terminal outcome."""
    idle = await wait_for_ci_idle(ctx, timeout_sec=timeout_sec, poll_sec=poll_sec)
    if idle.busy:
        return CiTerminalResult(
            outcome="timeout",
            provider="Azure Pipelines",
            label=idle.label,
            url=idle.url or "",
            detail=idle.detail or "Timed out while pipeline was still running.",
        )
    if idle.detail == "no_ci_observed":
        # One more look for a completed build that finished quickly
        latest = await get_latest_azdo_build_result(
            ctx.get("project") or "",
            repo_id=ctx.get("repo_id") or "",
            repo_name=ctx.get("repo_name") or "",
            commit_sha=commit_sha,
            min_created_at=created_at,
            require_sha_match=bool(commit_sha),
        )
        if latest:
            return latest
        return CiTerminalResult(
            outcome="no_ci",
            provider="Azure Pipelines",
            label=idle.label,
            url=idle.url or "",
            detail="No Azure Pipeline run was observed after merge.",
        )

    latest = await get_latest_azdo_build_result(
        ctx.get("project") or "",
        repo_id=ctx.get("repo_id") or "",
        repo_name=ctx.get("repo_name") or "",
        commit_sha=commit_sha,
        min_created_at=created_at,
        require_sha_match=bool(commit_sha),
    )
    if latest:
        return latest
    return CiTerminalResult(
        outcome="unknown",
        provider="Azure Pipelines",
        label=idle.label,
        url=idle.url or "",
        detail="Pipeline became idle but no matching completed build was found.",
    )


def format_ci_final_evaluation(watch: dict[str, Any], result: CiTerminalResult) -> str:
    outcome = result.outcome
    live = watch.get("live_url") or f"{settings.agent_app_url.rstrip('/')}/portal"
    lines = [
        "*Final evaluation*",
        "",
        f"1. Intent handled: `code_change` (Azure DevOps deploy)",
        f"2. Task: `{watch.get('task_id')}`",
        f"3. Merged to `main`: yes",
        f"4. Pipeline result: *{outcome}*",
    ]
    if result.url or watch.get("pipeline_url"):
        lines.append(f"5. Pipeline: {result.url or watch.get('pipeline_url')}")
    if watch.get("pr_url"):
        lines.append(f"6. Pull request: {watch['pr_url']}")
    if outcome in ("succeeded", "partiallySucceeded"):
        lines.append(f"7. Live portal: {live}")
        lines.append("8. Done — refresh your phone browser on /portal")
    elif outcome == "canceled":
        lines.append("7. Deployment was *canceled* — nothing new was confirmed live")
    elif outcome == "failed":
        lines.append("7. Deployment *failed* — check the pipeline logs, then retry")
    elif outcome == "timeout":
        lines.append("7. Still running when I last checked — open the pipeline link above")
    else:
        lines.append(f"7. Detail: {result.detail or outcome}")
    lines.append("")
    lines.append("*Next:* Reply *help* for the menu, or send another task.")
    return "\n".join(lines)


def format_ci_status_notification(watch: dict[str, Any], result: CiTerminalResult) -> str:
    outcome = result.outcome
    live = watch.get("live_url") or f"{settings.agent_app_url.rstrip('/')}/portal"
    if outcome in ("succeeded", "partiallySucceeded"):
        return (
            f"*Sunny's AI Agent* — Deployment completed (`{watch.get('task_id')}`)\n\n"
            f"*Azure Pipelines:* {outcome}\n"
            f"*Live portal:* {live}\n"
            f"*Pipeline:* {result.url or watch.get('pipeline_url')}\n"
            "Open on your *phone browser* and refresh."
        )
    if outcome == "canceled":
        return (
            f"*Sunny's AI Agent* — Deployment canceled (`{watch.get('task_id')}`)\n\n"
            f"*Azure Pipelines:* canceled\n"
            f"*Pipeline:* {result.url or watch.get('pipeline_url')}\n"
            "Nothing new was confirmed live."
        )
    if outcome == "failed":
        return (
            f"*Sunny's AI Agent* — Deployment failed (`{watch.get('task_id')}`)\n\n"
            f"*Azure Pipelines:* failed\n"
            f"*Pipeline:* {result.url or watch.get('pipeline_url')}\n"
            f"{result.detail}"
        )
    return (
        f"*Sunny's AI Agent* — Pipeline update (`{watch.get('task_id')}`)\n\n"
        f"*Azure Pipelines:* {outcome}\n"
        f"*Pipeline:* {result.url or watch.get('pipeline_url')}\n"
        f"{result.detail}"
    )


async def _send_whatsapp(phone: str, text: str) -> None:
    from agent.services.whatsapp import send_message

    if not phone or not text:
        return
    await send_message(phone, text)


async def notify_watch_finished(watch: dict[str, Any], result: CiTerminalResult) -> None:
    """Send deployment status + Final evaluation, then clear the durable watch."""
    task_id = watch.get("task_id") or ""
    phone = watch.get("phone") or ""
    try:
        await _send_whatsapp(phone, format_ci_status_notification(watch, result))
        await _send_whatsapp(phone, format_ci_final_evaluation(watch, result))
        await mark_ci_watch_notified(task_id)
        await clear_ci_watch(task_id)
        logger.info(
            "Sent AzDO CI final WhatsApp for task %s outcome=%s",
            task_id,
            result.outcome,
        )
    except Exception:
        logger.exception("Failed notifying AzDO CI watch for task %s", task_id)


async def watch_azdo_pipeline_and_notify(task_id: str) -> None:
    """Background poller — safe to call after merge; survives via DB + startup resume."""
    if not task_id or task_id in _running_watches:
        return
    _running_watches.add(task_id)
    try:
        watches = await list_open_ci_watches()
        watch = next((w for w in watches if w["task_id"] == task_id), None)
        if not watch:
            return

        ctx = {
            "provider": "azure_devops",
            "project": watch.get("project") or "",
            "repo_name": watch.get("repo_name") or "",
            "repo_id": watch.get("repo_id") or "",
        }
        created_at = float(watch.get("created_at") or time.time())
        commit_sha = watch.get("commit_sha") or ""

        busy = await check_ci_busy_for_context(ctx)
        if busy.busy:
            result = await wait_for_azdo_terminal(
                ctx,
                commit_sha=commit_sha,
                created_at=created_at,
                timeout_sec=1500,
                poll_sec=20,
            )
            await notify_watch_finished(watch, result)
            return

        # Pipeline idle — only notify if a completed build matches THIS merge commit.
        existing = await get_latest_azdo_build_result(
            ctx["project"],
            repo_id=ctx["repo_id"],
            repo_name=ctx["repo_name"],
            commit_sha=commit_sha,
            min_created_at=created_at,
            require_sha_match=bool(commit_sha),
        )
        if existing:
            await notify_watch_finished(watch, existing)
            return

        result = await wait_for_azdo_terminal(
            ctx,
            commit_sha=commit_sha,
            created_at=created_at,
            timeout_sec=1500,
            poll_sec=20,
        )
        await notify_watch_finished(watch, result)
    except Exception:
        logger.exception("AzDO CI watch failed for task %s", task_id)
    finally:
        _running_watches.discard(task_id)


def start_azdo_ci_watch(task_id: str) -> None:
    """Fire-and-forget watcher (does not block GitHub paths)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("No running loop to start CI watch for %s", task_id)
        return
    loop.create_task(watch_azdo_pipeline_and_notify(task_id), name=f"ci-watch-{task_id}")


async def resume_pending_ci_watches() -> None:
    """Called on app startup after AzDO self-deploy restarts the agent."""
    try:
        watches = await list_open_ci_watches()
    except Exception:
        logger.exception("Failed listing pending CI watches at startup")
        return
    if not watches:
        return
    logger.info("Resuming %d durable AzDO CI watch(es)", len(watches))
    for w in watches:
        start_azdo_ci_watch(w["task_id"])
