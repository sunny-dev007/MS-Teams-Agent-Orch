"""Build enterprise release notes from Azure DevOps PR evidence + LLM."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import azure_devops as azdo
from agent.services.llm import invoke_llm

logger = get_logger(__name__)

_SYSTEM = """You are a senior technical writer for enterprise software releases.
Given Azure DevOps pull request evidence, produce professional release notes as JSON only
(no markdown fences) with this schema:
{
  "title": "string",
  "executive_summary": "2-4 sentences for leadership",
  "business_impact": ["bullet", "..."],
  "features": ["bullet", "..."],
  "fixes": ["bullet", "..."],
  "breaking_changes": ["bullet or empty"],
  "security_compliance": ["bullet or empty"],
  "deployment_notes": ["bullet", "..."],
  "testing_validation": ["bullet", "..."],
  "known_issues": ["bullet or empty"],
  "upgrade_steps": ["numbered-style bullet", "..."],
  "reviewer_highlights": ["important PR comment themes", "..."]
}
Rules:
- Base claims only on provided evidence (title, description, commits, files, comments).
- If evidence is thin, say so clearly in executive_summary; do not invent product features.
- Prefer precise, enterprise tone (US/UK customer ready). No emojis.
"""


def _truncate(text: str, limit: int = 4000) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 20] + "\n…(truncated)"


def _file_paths(changes: list[dict]) -> list[str]:
    paths: list[str] = []
    for ch in changes[:80]:
        item = ch.get("item") or {}
        path = item.get("path") or ch.get("path") or ""
        if path:
            paths.append(str(path))
    return paths


def _comment_snippets(threads: list[dict]) -> list[str]:
    out: list[str] = []
    for th in threads[:40]:
        if th.get("isDeleted"):
            continue
        comments = th.get("comments") or []
        for c in comments[:3]:
            if c.get("isDeleted") or c.get("commentType") == "system":
                continue
            content = (c.get("content") or "").strip()
            if content:
                author = ((c.get("author") or {}).get("displayName")) or "reviewer"
                out.append(f"{author}: {_truncate(content, 400)}")
            if len(out) >= 25:
                return out
    return out


async def gather_pr_evidence(
    *,
    pr_id: str | int,
    project: str | None = None,
    repo_name: str | None = None,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Fetch PR metadata, commits, file list, and review comments from AzDO."""
    project = (project or settings.azdo_demo_project or "Project-NIT").strip()
    preferred_repo = (repo_name or settings.azdo_demo_repo or "").strip()
    pid = int(pr_id)

    repo: dict[str, Any] | None = None
    pr: dict[str, Any] | None = None

    if repo_id:
        repo = {"id": repo_id, "name": preferred_repo or repo_id}
        pr = await azdo.get_pull_request(project, repo_id, pid)
    else:
        found = await azdo.find_pull_request_across_repos(
            project, pid, preferred_repo=preferred_repo or None
        )
        if not found:
            raise RuntimeError(
                f"Could not find PR #{pid} in project {project}. "
                "Confirm the PR exists and AZDO_PAT can read the repo."
            )
        repo, pr = found

    rid = str(repo.get("id"))
    commits = await azdo.get_pull_request_commits(project, rid, pid)
    threads = await azdo.get_pull_request_threads(project, rid, pid)
    changes = await azdo.get_pull_request_changes(project, rid, pid)
    files = _file_paths(changes)
    comments = _comment_snippets(threads)

    created_by = ((pr.get("createdBy") or {}).get("displayName")) or ""
    reviewers = [
        (r.get("displayName") or (r.get("uniqueName") or ""))
        for r in (pr.get("reviewers") or [])
        if (r.get("displayName") or r.get("uniqueName"))
    ]
    source = (pr.get("sourceRefName") or "").replace("refs/heads/", "")
    target = (pr.get("targetRefName") or "").replace("refs/heads/", "")
    last_merge = pr.get("lastMergeSourceCommit") or pr.get("lastMergeCommit") or {}
    commit_sha = (last_merge.get("commitId") or "")[:12]

    commit_lines = []
    for c in commits[:30]:
        msg = (c.get("comment") or c.get("message") or "").split("\n")[0].strip()
        sha = (c.get("commitId") or "")[:8]
        if msg:
            commit_lines.append(f"{sha} {msg}")

    org = settings.azdo_org_url.rstrip("/")
    pr_url = (
        f"{org}/{project}/_git/{repo.get('name')}/pullrequest/{pid}"
        if repo.get("name")
        else ""
    )

    return {
        "project": project,
        "repo_name": repo.get("name") or preferred_repo,
        "repo_id": rid,
        "pr_id": str(pid),
        "pr_title": pr.get("title") or f"PR {pid}",
        "pr_description": _truncate(pr.get("description") or "", 6000),
        "pr_status": pr.get("status") or "",
        "pr_url": pr_url,
        "created_by": created_by,
        "reviewers": reviewers,
        "source_branch": source,
        "target_branch": target,
        "commit_sha": commit_sha,
        "commits": commit_lines,
        "files_changed": files[:100],
        "file_count": len(files),
        "review_comments": comments,
        "merge_status": pr.get("mergeStatus") or "",
    }


async def draft_release_notes_sections(evidence: dict[str, Any]) -> dict[str, Any]:
    """LLM draft; falls back to deterministic template if LLM fails."""
    payload = {
        "pr_title": evidence.get("pr_title"),
        "pr_description": evidence.get("pr_description"),
        "pr_status": evidence.get("pr_status"),
        "repo": evidence.get("repo_name"),
        "branches": f"{evidence.get('source_branch')} → {evidence.get('target_branch')}",
        "author": evidence.get("created_by"),
        "reviewers": evidence.get("reviewers"),
        "commits": evidence.get("commits"),
        "files_changed": evidence.get("files_changed"),
        "review_comments": evidence.get("review_comments"),
    }
    try:
        resp = await invoke_llm(
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)[:12000]),
            ],
            temperature=0.2,
            role="default",
        )
        raw = (resp.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end] if start != -1 and end > 0 else raw)
        if isinstance(data, dict) and data.get("executive_summary"):
            return data
    except Exception:
        logger.exception("LLM release-notes draft failed — using evidence template")

    return _fallback_sections(evidence)


def _fallback_sections(evidence: dict[str, Any]) -> dict[str, Any]:
    desc = (evidence.get("pr_description") or "").strip()
    commits = evidence.get("commits") or []
    files = evidence.get("files_changed") or []
    comments = evidence.get("review_comments") or []
    return {
        "title": f"Release Notes — {evidence.get('pr_title')}",
        "executive_summary": desc[:800]
        or (
            f"Release documentation for PR #{evidence.get('pr_id')} "
            f"in {evidence.get('repo_name')} "
            f"({evidence.get('source_branch')} → {evidence.get('target_branch')}). "
            f"{len(commits)} commit(s), {len(files)} file(s) changed."
        ),
        "business_impact": [
            "Improves delivery documentation and auditability for this change set."
        ],
        "features": [c for c in commits if not c.lower().startswith(("fix", "bug"))][:8]
        or ["See commits and file list below."],
        "fixes": [c for c in commits if c.lower().startswith(("fix", "bug"))][:8],
        "breaking_changes": [],
        "security_compliance": [],
        "deployment_notes": [
            f"Target branch: {evidence.get('target_branch') or 'main'}",
            f"App URL: {settings.agent_app_url}",
            "Validate health endpoints after deploy.",
        ],
        "testing_validation": [
            "Peer / AI PR review completed where applicable.",
            "Confirm CI pipeline result for the merge commit.",
        ],
        "known_issues": [],
        "upgrade_steps": [
            "Merge PR to main (if not already).",
            "Wait for production pipeline Deploy stage.",
            "Verify /health and Copilot channel health.",
        ],
        "reviewer_highlights": comments[:8],
    }


def _ul(items: list[Any]) -> str:
    cleaned = [str(i).strip() for i in (items or []) if str(i).strip()]
    if not cleaned:
        return "<p><em>None noted.</em></p>"
    return "<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in cleaned) + "</ul>"


def render_enterprise_html(
    *,
    evidence: dict[str, Any],
    sections: dict[str, Any],
    event: dict[str, Any],
) -> str:
    title = sections.get("title") or event.get("title") or f"Release Notes — PR {evidence.get('pr_id')}"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    files = evidence.get("files_changed") or []
    commits = evidence.get("commits") or []
    file_preview = files[:40]
    more_files = max(0, len(files) - len(file_preview))

    meta_rows = [
        ("Release ID", event.get("release_id") or ""),
        ("PR", f"#{evidence.get('pr_id')} — {evidence.get('pr_title')}"),
        ("Repository", f"{evidence.get('project')} / {evidence.get('repo_name')}"),
        ("Author", evidence.get("created_by") or "n/a"),
        ("Reviewers", ", ".join(evidence.get("reviewers") or []) or "n/a"),
        ("Branches", f"{evidence.get('source_branch')} → {evidence.get('target_branch')}"),
        ("PR status", evidence.get("pr_status") or "n/a"),
        ("Commit", evidence.get("commit_sha") or event.get("commit_sha") or "n/a"),
        ("Pipeline", event.get("pipeline_id") or "n/a"),
        ("Build", event.get("build_id") or "n/a"),
        ("Environment", event.get("env") or "prod"),
        ("App URL", event.get("app_url") or settings.agent_app_url),
        ("QA report", event.get("qa_report_url") or "pending"),
        ("PR link", evidence.get("pr_url") or "n/a"),
    ]
    meta_html = "".join(
        f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in meta_rows
    )

    file_count_label = html.escape(str(evidence.get("file_count") or len(files)))
    more_files_html = f"<p class='mono'>+{more_files} more files…</p>" if more_files else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(str(title))}</title>
<style>
body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 0; color: #242424; background: #fff; }}
.wrap {{ max-width: 920px; margin: 0 auto; padding: 2rem 1.5rem 3rem; }}
h1 {{ color: #0f6cbd; margin-bottom: 0.25rem; }}
.sub {{ color: #605e5c; margin-bottom: 1.5rem; }}
h2 {{ color: #323130; border-bottom: 1px solid #edebe9; padding-bottom: 0.35rem; margin-top: 1.75rem; }}
table.meta {{ border-collapse: collapse; width: 100%; margin: 1rem 0 1.5rem; }}
table.meta th, table.meta td {{ border: 1px solid #e1dfdd; padding: 8px 12px; text-align: left; vertical-align: top; }}
table.meta th {{ width: 180px; background: #f3f2f1; }}
code, .mono {{ font-family: Consolas, monospace; font-size: 0.92em; }}
.footer {{ margin-top: 2.5rem; color: #605e5c; font-size: 0.9rem; }}
.badge {{ display: inline-block; background: #e8f3ff; color: #0f6cbd; padding: 2px 8px; border-radius: 4px; font-size: 0.85rem; }}
</style></head>
<body><div class="wrap">
<p><span class="badge">Enterprise Release Notes</span></p>
<h1>{html.escape(str(title))}</h1>
<p class="sub">Documentation Agent · Release Agent Fabric · {html.escape(generated)}</p>

<table class="meta">{meta_html}</table>

<h2>1. Executive summary</h2>
<p>{html.escape(str(sections.get("executive_summary") or ""))}</p>

<h2>2. Business impact</h2>
{_ul(sections.get("business_impact") or [])}

<h2>3. Features</h2>
{_ul(sections.get("features") or [])}

<h2>4. Fixes</h2>
{_ul(sections.get("fixes") or [])}

<h2>5. Breaking changes</h2>
{_ul(sections.get("breaking_changes") or [])}

<h2>6. Security &amp; compliance</h2>
{_ul(sections.get("security_compliance") or [])}

<h2>7. Deployment notes</h2>
{_ul(sections.get("deployment_notes") or [])}

<h2>8. Testing &amp; validation</h2>
{_ul(sections.get("testing_validation") or [])}

<h2>9. Known issues</h2>
{_ul(sections.get("known_issues") or [])}

<h2>10. Upgrade / rollout steps</h2>
{_ul(sections.get("upgrade_steps") or [])}

<h2>11. Reviewer highlights</h2>
{_ul(sections.get("reviewer_highlights") or [])}

<h2>12. Commits</h2>
{_ul(commits) if commits else "<p><em>No commits returned from Azure DevOps.</em></p>"}

<h2>13. Files changed ({file_count_label})</h2>
{_ul(file_preview)}
{more_files_html}

<p class="footer">Generated by Documentation Agent · Sunny Personal AI Agent · Evidence from Azure DevOps PR API · {html.escape(generated)}</p>
</div></body></html>"""


async def build_release_notes_document(
    *,
    pr_id: str | int,
    event: dict[str, Any],
    project: str | None = None,
    repo_name: str | None = None,
    repo_id: str | None = None,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Return (title, html, evidence, sections)."""
    evidence = await gather_pr_evidence(
        pr_id=pr_id,
        project=project,
        repo_name=repo_name,
        repo_id=repo_id,
    )
    sections = await draft_release_notes_sections(evidence)
    title = str(sections.get("title") or f"Release Notes — PR {pr_id}")
    html_body = render_enterprise_html(evidence=evidence, sections=sections, event=event)
    return title, html_body, evidence, sections
