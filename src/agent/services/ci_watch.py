"""Durable Azure DevOps pipeline watches that survive App Service restarts.

Why: AzDO deploys for web.Whatsapp-AI-Agent restart this same App Service, which
kills the in-process wait inside coding_deployer — so WhatsApp never got a Final
evaluation. GitHub sample deploys target a different app and stay unchanged.
"""

from __future__ import annotations

import asyncio
import datetime
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.config import settings
from agent.core.logging import get_logger
from agent.models.db import Base, async_session, ensure_db_schema

logger = get_logger(__name__)

# Avoid duplicate background watchers for the same task in one process.
_running_watches: set[str] = set()

TERMINAL_OUTCOMES = frozenset({"succeeded", "failed", "canceled", "partiallySucceeded"})

_PIPELINE_DEF_RE = re.compile(r"[?&]definitionId=(\d+)", re.I)


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


def _pipeline_definition_id(pipeline_url: str) -> str:
    match = _PIPELINE_DEF_RE.search(pipeline_url or "")
    return match.group(1) if match else ""


def _build_from_api(build: dict, *, project: str, repo_name: str, org: str) -> CiTerminalResult:
    build_id = str(build.get("id") or "")
    outcome = _normalize_outcome(build.get("result"))
    defn = (build.get("definition") or {}).get("name") or "pipeline"
    url = (
        f"{org}/{project}/_build/results?buildId={build_id}"
        if build_id
        else f"{org}/{project}/_build"
    )
    detail = f"Build *{defn}* #{build_id} finished with `{outcome}`."
    if outcome == "failed":
        detail += " Check the *Run tests* step in Azure Pipelines."
    return CiTerminalResult(
        outcome=outcome or "unknown",
        provider="Azure Pipelines",
        label=f"{project}/{repo_name}" if repo_name else project,
        url=url,
        detail=detail,
        build_id=build_id,
    )


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
    pipeline_definition_id: str = "",
) -> CiTerminalResult | None:
    """Return the newest completed AzDO build for this repo (optional SHA / definition match)."""
    from agent.services import azure_devops as azdo

    if not project:
        return None
    try:
        builds = await azdo.list_builds(
            project,
            status_filter="2",  # completed
            top=20,
            repository_id=repo_id or None,
            definition_ids=pipeline_definition_id or None,
        )
    except Exception:
        logger.exception("Failed listing completed AzDO builds for %s", project)
        return None

    wanted_repo = (repo_name or "").strip().lower()
    wanted_sha = (commit_sha or "").strip().lower()
    wanted_def = str(pipeline_definition_id or "").strip()
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
        defn_id = str((b.get("definition") or {}).get("id") or "")
        if wanted_repo and repo_ref and repo_ref != wanted_repo:
            continue
        if repo_id and rid and rid != str(repo_id):
            continue
        if wanted_def and defn_id and defn_id != wanted_def:
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

    outcome = _normalize_outcome(build.get("result"))
    if not outcome or outcome == "unknown":
        return None
    return _build_from_api(build, project=project, repo_name=repo_name, org=org)


async def resolve_build_for_watch(watch: dict[str, Any]) -> CiTerminalResult | None:
    """Match merge pipeline by SHA first, then definitionId + finish time."""
    project = watch.get("project") or ""
    repo_id = watch.get("repo_id") or ""
    repo_name = watch.get("repo_name") or ""
    created_at = float(watch.get("created_at") or 0)
    commit_sha = (watch.get("commit_sha") or "").strip()
    def_id = _pipeline_definition_id(watch.get("pipeline_url") or "")

    common = dict(
        project=project,
        repo_id=repo_id,
        repo_name=repo_name,
        min_created_at=created_at,
        pipeline_definition_id=def_id,
    )

    if commit_sha:
        matched = await get_latest_azdo_build_result(
            **common,
            commit_sha=commit_sha,
            require_sha_match=True,
        )
        if matched:
            return matched

    return await get_latest_azdo_build_result(
        **common,
        commit_sha="",
        require_sha_match=False,
    )


async def poll_watch_until_terminal(
    watch: dict[str, Any],
    *,
    timeout_sec: int = 1500,
    poll_sec: int = 15,
) -> CiTerminalResult:
    """Poll completed builds until terminal outcome (handles fast-failing pipelines)."""
    deadline = time.time() + timeout_sec
    pipeline_url = watch.get("pipeline_url") or ""
    org = settings.azdo_org_url.rstrip("/")
    project = watch.get("project") or ""

    while time.time() < deadline:
        result = await resolve_build_for_watch(watch)
        if result and result.outcome in TERMINAL_OUTCOMES:
            logger.info(
                "CI watch task=%s matched build %s outcome=%s",
                watch.get("task_id"),
                result.build_id,
                result.outcome,
            )
            return result

        from agent.services.ci_gate import check_ci_busy_for_context

        ctx = {
            "provider": "azure_devops",
            "project": project,
            "repo_name": watch.get("repo_name") or "",
            "repo_id": watch.get("repo_id") or "",
        }
        busy = await check_ci_busy_for_context(ctx)
        if not busy.busy and result and result.outcome in TERMINAL_OUTCOMES:
            return result

        await asyncio.sleep(poll_sec)

    return CiTerminalResult(
        outcome="timeout",
        provider="Azure Pipelines",
        label=f"{project}/{watch.get('repo_name') or ''}",
        url=pipeline_url or f"{org}/{project}/_build",
        detail="Pipeline still running or result not visible yet — open the pipeline link.",
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
    watch = {
        "project": ctx.get("project") or "",
        "repo_name": ctx.get("repo_name") or "",
        "repo_id": ctx.get("repo_id") or "",
        "commit_sha": commit_sha,
        "created_at": created_at or time.time(),
        "pipeline_url": ctx.get("pipeline_url") or "",
        "task_id": ctx.get("task_id") or "",
    }
    return await poll_watch_until_terminal(
        watch, timeout_sec=timeout_sec, poll_sec=poll_sec
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
    from agent.services.channel_notify import send_channel_message

    if not phone or not text:
        return
    await send_channel_message(phone, text)


async def notify_watch_finished(watch: dict[str, Any], result: CiTerminalResult) -> None:
    """Send CI status. On test/build failure, ask Sunny whether test_fixer should repair.

    Session context is preserved (ci_fix_approval / pipeline gate) until STOP or
    check my repos — we do not wipe the WhatsApp conversation here.
    """
    task_id = watch.get("task_id") or ""
    phone = watch.get("phone") or ""
    notes = (watch.get("notes") or "").lower()
    is_pr_validation = "pr_validation" in notes or "ci_fix_validation" in notes
    try:
        if result.outcome == "failed" and settings.enable_ci_test_fix_agent and phone:
            await _offer_ci_test_fix(watch, result)
        elif is_pr_validation:
            await _send_whatsapp(
                phone, format_pr_validation_notification(watch, result)
            )
            # Do not clear PR review / deploy gates — only record CI outcome.
            if phone and result.ok:
                try:
                    from agent.core.session import save_session

                    await save_session(
                        phone,
                        data={
                            "last_ci_outcome": result.outcome,
                            "pipeline_url": result.url or watch.get("pipeline_url") or "",
                        },
                        merge_data=True,
                    )
                except Exception:
                    logger.exception("Failed saving PR CI outcome for %s", phone)
        else:
            await _send_whatsapp(phone, format_ci_status_notification(watch, result))
            await _send_whatsapp(phone, format_ci_final_evaluation(watch, result))
            # Post-merge success: clear blocking gate but keep task memory
            if phone and result.ok:
                try:
                    from agent.core.session import save_session

                    await save_session(
                        phone,
                        awaiting=None,
                        clear_awaiting=True,
                        merge_data=True,
                        data={
                            "last_completed_task_id": task_id,
                            "last_pr_url": watch.get("pr_url") or "",
                            "pending_task_id": task_id,
                        },
                    )
                except Exception:
                    logger.exception("Failed saving post-CI session memory for %s", phone)
        await mark_ci_watch_notified(task_id)
        await clear_ci_watch(task_id)
        logger.info(
            "Sent AzDO CI final WhatsApp for task %s outcome=%s",
            task_id,
            result.outcome,
        )
    except Exception:
        logger.exception("Failed notifying AzDO CI watch for task %s", task_id)


def format_pr_validation_notification(
    watch: dict[str, Any], result: CiTerminalResult
) -> str:
    task_id = watch.get("task_id") or ""
    if result.ok:
        return (
            f"*Sunny's AI Agent* — PR checks passed (`{task_id}`)\n\n"
            f"*Azure Pipelines:* {result.outcome} (validate only — *not* live deploy)\n"
            f"*Pipeline:* {result.url or watch.get('pipeline_url')}\n"
            f"*PR:* {watch.get('pr_url') or 'N/A'}\n\n"
            "Deploy to App Service is *skipped* on the agent branch on purpose.\n"
            "To go live: finish *AI review* (reply *1*), then *APPROVE* — "
            "that merges to `main` and runs the real Deploy stage.\n\n"
            "Context is kept until *STOP* or *check my repos*."
        )
    return (
        f"*Sunny's AI Agent* — PR checks update (`{task_id}`)\n\n"
        f"*Azure Pipelines:* {result.outcome}\n"
        f"*Pipeline:* {result.url or watch.get('pipeline_url')}\n"
        f"{result.detail}"
    )


async def _offer_ci_test_fix(watch: dict[str, Any], result: CiTerminalResult) -> None:
    """Persist ci_fix_approval gate and ask Sunny to approve AI test repair."""
    from agent.workflow.gates import persist_ci_fix_gate

    task_id = watch.get("task_id") or ""
    phone = watch.get("phone") or ""
    project = watch.get("project") or ""
    summary = ""
    if result.build_id and project:
        try:
            from agent.services.azure_devops import get_build_failure_summary

            summary = await get_build_failure_summary(project, result.build_id)
        except Exception:
            logger.exception("Could not load failure summary for build %s", result.build_id)

    phase = "pr_validation"
    notes = (watch.get("notes") or "").lower()
    if "post_merge" in notes or "deploy" in notes:
        phase = "post_merge"
    elif "ci_fix" in notes:
        phase = "ci_fix_validation"

    excerpt = summary.strip()
    if len(excerpt) > 900:
        excerpt = excerpt[-900:]

    state = {
        "task_id": task_id,
        "pending_task_id": task_id,
        "repo_provider": "azure_devops",
        "azdo_project": project,
        "azdo_repo_id": watch.get("repo_id") or "",
        "repo_name": watch.get("repo_name") or "",
        "repo_url": _repo_url_from_watch(watch),
        "pr_url": watch.get("pr_url") or "",
        "pipeline_url": result.url or watch.get("pipeline_url") or "",
        "commit_sha": watch.get("commit_sha") or "",
        "ci_build_id": result.build_id,
        "ci_failure_summary": summary,
        "ci_watch_phase": phase,
        "pipeline_status": "failed",
        "user_message": f"Fix CI test failures for task {task_id}",
    }
    await persist_ci_fix_gate(phone, state)

    msg = (
        f"*Sunny's AI Agent* — Build / tests failed (`{task_id}`)\n\n"
        f"*Azure Pipelines:* failed\n"
        f"*Pipeline:* {result.url or watch.get('pipeline_url')}\n"
        f"{result.detail}\n\n"
    )
    if excerpt:
        msg += f"*Failure excerpt:*\n```\n{excerpt}\n```\n\n"
    msg += (
        "I can run the *test_fixer* agent to identify and repair the failing tests "
        "(and related code) without losing this task's context.\n\n"
        f"Reply *FIX TESTS {task_id}* (or just *FIX TESTS*) to let AI fix it.\n"
        f"Reply *SKIP {task_id}* to leave the failure as-is.\n"
        "Reply *STOP* only when you want to clear the whole session."
    )
    await _send_whatsapp(phone, msg)


def _repo_url_from_watch(watch: dict[str, Any]) -> str:
    project = watch.get("project") or ""
    repo = watch.get("repo_name") or ""
    org = settings.azdo_org_url.rstrip("/")
    if project and repo and org:
        return f"{org}/{project}/_git/{repo}"
    return ""


async def watch_azdo_pipeline_and_notify(task_id: str) -> None:
    """Background poller — safe to call after merge; survives via DB + startup resume."""
    if not task_id or task_id in _running_watches:
        return
    _running_watches.add(task_id)
    watch: dict[str, Any] | None = None
    try:
        watches = await list_open_ci_watches()
        watch = next((w for w in watches if w["task_id"] == task_id), None)
        if not watch:
            return

        result = await poll_watch_until_terminal(
            watch, timeout_sec=1500, poll_sec=15
        )
        await notify_watch_finished(watch, result)
    except Exception as exc:
        logger.exception("AzDO CI watch failed for task %s", task_id)
        if watch and watch.get("phone"):
            try:
                await notify_watch_finished(
                    watch,
                    CiTerminalResult(
                        outcome="unknown",
                        url=watch.get("pipeline_url") or "",
                        detail=(
                            "Could not confirm the Azure Pipeline result after merge. "
                            f"Open the pipeline link to check *Run tests*. ({exc})"
                        ),
                    ),
                )
            except Exception:
                logger.exception("Failed error notify for CI watch %s", task_id)
    finally:
        _running_watches.discard(task_id)


async def tick_open_ci_watches() -> None:
    """Safety net — restart watchers for durable rows still open (survives app recycle)."""
    try:
        watches = await list_open_ci_watches()
    except Exception:
        logger.exception("CI watch ticker failed listing watches")
        return
    for w in watches:
        start_azdo_ci_watch(w["task_id"])


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
