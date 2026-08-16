"""QA Agent — HTTP smoke against release app_url + SharePoint report.

Feature: Release Agent Fabric. ENABLE_QA_AGENT default false — no prod impact.
Playwright browser suite can replace/extend http smoke later without changing Teams UX.
"""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.models.release_event import (
    STATUS_QA_FAILED,
    STATUS_QA_PASSED,
    STATUS_QA_PENDING,
    STATUS_QA_RUNNING,
    find_release_event,
    upsert_release_event,
)
from agent.services.qa_smoke import render_qa_report_html, run_http_smoke, upload_qa_report_html

logger = get_logger(__name__)

AGENT_NAME = "qa_agent"

_PR_RE = re.compile(r"\b(?:pr|pull\s*request)\s*#?\s*(\d+)\b", re.I)
_PIPELINE_RE = re.compile(r"\b(?:pipeline|build)\s*#?\s*(\d+)\b", re.I)
_RELEASE_RE = re.compile(r"\b(rel_[a-f0-9]+)\b", re.I)


async def run_qa_smoke(state: AgentState) -> AgentState:
    if not settings.enable_qa_agent:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*QA Agent* is installed but **disabled** (ENABLE_QA_AGENT=false).\n\n"
                "When enabled it runs smoke checks against the release `app_url` "
                "and attaches a report to the ReleaseEvent.\n"
                "Dev Agent and WhatsApp paths are unchanged."
            ),
        }

    msg = state.get("user_message") or ""
    pr = _PR_RE.search(msg)
    pipe = _PIPELINE_RE.search(msg)
    rel = _RELEASE_RE.search(msg)
    event = None
    if rel:
        from agent.models.release_event import get_release_event

        event = await get_release_event(rel.group(1))
    if event is None:
        event = await find_release_event(
            pr_id=pr.group(1) if pr else None,
            pipeline_id=pipe.group(1) if pipe else None,
            build_id=state.get("ci_build_id"),
        )

    app_url = (
        state.get("app_url")
        or (event or {}).get("app_url")
        or settings.agent_app_url
        or settings.sample_app_url
    )

    if event is None:
        event = await upsert_release_event(
            pr_id=pr.group(1) if pr else None,
            pipeline_id=pipe.group(1) if pipe else None,
            app_url=app_url,
            status=STATUS_QA_PENDING,
            requested_by=state.get("whatsapp_phone"),
            channel="teams" if str(state.get("whatsapp_phone") or "").startswith("teams:") else "whatsapp",
            title="QA run",
            summary=msg[:1000],
        )

    await upsert_release_event(
        release_id=event["release_id"],
        status=STATUS_QA_RUNNING,
        app_url=app_url,
    )

    try:
        report = await run_http_smoke(app_url)
        overall = report.get("overall")
        status = STATUS_QA_PASSED if overall == "passed" else STATUS_QA_FAILED
        html_body = render_qa_report_html(
            report,
            release_id=event["release_id"],
            pr_id=str(event.get("pr_id") or (pr.group(1) if pr else "")),
        )
        report_url = ""
        try:
            report_url = await upload_qa_report_html(
                title=f"QA-{event['release_id']}",
                html_body=html_body,
            )
        except Exception:
            logger.exception("QA report SharePoint upload failed")

        artifacts = list(event.get("artifacts") or [])
        if report_url:
            artifacts.append({"type": "qa_smoke_report", "url": report_url})

        updated = await upsert_release_event(
            release_id=event["release_id"],
            status=status,
            app_url=app_url,
            qa_report_url=report_url or None,
            artifacts=artifacts,
            summary=(
                f"QA {overall}: {report.get('passed')}/{report.get('total')} checks passed "
                f"against {app_url}"
            ),
            extra={"qa_report": report},
        )

        icon = "OK" if status == STATUS_QA_PASSED else "FAIL"
        note = (
            f"*QA Agent* — smoke {overall} [{icon}]\n\n"
            f"*Release:* `{updated['release_id']}`\n"
            f"*App URL:* {app_url}\n"
            f"*PR:* {updated.get('pr_id') or 'n/a'} · *Pipeline:* {updated.get('pipeline_id') or 'n/a'}\n"
            f"*Checks:* {report.get('passed')}/{report.get('total')} passed\n"
        )
        if report_url:
            note += f"*Report:* {report_url}\n"
        else:
            note += "_Report HTML generated; SharePoint upload skipped/failed — check Graph config._\n"
        if status == STATUS_QA_PASSED:
            note += "\n_Next: say *write release notes for " + (
                f"PR {updated['pr_id']}" if updated.get("pr_id") else updated["release_id"]
            ) + "* to publish Docs with QA link._\n"
        else:
            failed = [c["name"] for c in (report.get("checks") or []) if not c.get("passed")]
            note += f"\nFailed checks: {', '.join(failed) or 'unknown'}\n"

        return {
            **state,
            "status": "completed" if status == STATUS_QA_PASSED else "failed",
            "handled_by": AGENT_NAME,
            "release_id": updated["release_id"],
            "notification_text": note,
            "qa_report_url": report_url,
        }
    except Exception as exc:
        logger.exception("QA Agent failed")
        await upsert_release_event(release_id=event["release_id"], status=STATUS_QA_FAILED)
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "release_id": event["release_id"],
            "notification_text": f"*QA Agent* failed: {exc}",
            "error": str(exc),
        }
