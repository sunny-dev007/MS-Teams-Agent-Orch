"""QA smoke runner — HTTP health checks (App Service safe).

Full browser Playwright can be added later on a worker/Foundry host.
This module produces a pass/fail report suitable for enterprise demos.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin

import httpx

from agent.core.logging import get_logger
from agent.services import ms_graph

logger = get_logger(__name__)


async def run_http_smoke(app_url: str) -> dict[str, Any]:
    """Run lightweight smoke checks against a deployed app URL."""
    base = (app_url or "").rstrip("/") + "/"
    checks: list[dict[str, Any]] = []
    targets = [
        ("GET", "health", "/health", [200]),
        ("GET", "copilot_health", "/api/channels/copilot/health", [200]),
        ("GET", "portal", "/portal", [200, 302, 307, 308]),
        ("GET", "openapi", "/openapi.json", [200]),
    ]
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        for method, name, path, ok_codes in targets:
            url = urljoin(base, path.lstrip("/"))
            started = datetime.now(timezone.utc)
            try:
                resp = await client.request(method, url)
                elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                passed = resp.status_code in ok_codes
                snippet = ""
                try:
                    snippet = (resp.text or "")[:240]
                except Exception:
                    snippet = ""
                checks.append(
                    {
                        "name": name,
                        "url": url,
                        "status_code": resp.status_code,
                        "ok_codes": ok_codes,
                        "passed": passed,
                        "elapsed_ms": elapsed_ms,
                        "snippet": snippet,
                    }
                )
            except Exception as exc:
                elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                checks.append(
                    {
                        "name": name,
                        "url": url,
                        "status_code": None,
                        "ok_codes": ok_codes,
                        "passed": False,
                        "elapsed_ms": elapsed_ms,
                        "error": str(exc),
                        "snippet": "",
                    }
                )

    passed_n = sum(1 for c in checks if c.get("passed"))
    failed_n = len(checks) - passed_n
    overall = "passed" if failed_n == 0 else "failed"
    return {
        "overall": overall,
        "passed": passed_n,
        "failed": failed_n,
        "total": len(checks),
        "app_url": app_url,
        "checks": checks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner": "http_smoke_v1",
    }


def render_qa_report_html(report: dict[str, Any], *, release_id: str = "", pr_id: str = "") -> str:
    rows = []
    for c in report.get("checks") or []:
        status = "PASS" if c.get("passed") else "FAIL"
        code = c.get("status_code")
        err = c.get("error") or ""
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(c.get('name')))}</td>"
            f"<td class='mono'>{html.escape(str(c.get('url')))}</td>"
            f"<td>{html.escape(str(code if code is not None else '—'))}</td>"
            f"<td>{c.get('elapsed_ms')} ms</td>"
            f"<td><strong>{status}</strong></td>"
            f"<td>{html.escape(err or (c.get('snippet') or '')[:120])}</td>"
            "</tr>"
        )
    overall = str(report.get("overall") or "unknown").upper()
    color = "#107c10" if overall == "PASSED" else "#d13438"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>QA Smoke Report</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 2rem; color: #242424; }}
h1 {{ color: #0f6cbd; }}
.badge {{ display:inline-block; padding:4px 10px; border-radius:4px; color:#fff; background:{color}; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
th, td {{ border: 1px solid #e1dfdd; padding: 8px; text-align: left; vertical-align: top; }}
th {{ background: #f3f2f1; }}
.mono {{ font-family: Consolas, monospace; font-size: 0.9em; }}
</style></head>
<body>
<h1>QA Smoke Report</h1>
<p><span class="badge">{html.escape(overall)}</span>
&nbsp; {report.get('passed')}/{report.get('total')} checks passed</p>
<p><strong>App URL:</strong> {html.escape(str(report.get('app_url') or ''))}<br>
<strong>Release:</strong> {html.escape(release_id or 'n/a')} &nbsp;
<strong>PR:</strong> {html.escape(pr_id or 'n/a')}<br>
<strong>Runner:</strong> {html.escape(str(report.get('runner') or ''))} &nbsp;
<strong>Generated:</strong> {html.escape(str(report.get('generated_at') or ''))}</p>
<table>
<tr><th>Check</th><th>URL</th><th>HTTP</th><th>Latency</th><th>Result</th><th>Detail</th></tr>
{''.join(rows)}
</table>
<p><em>Generated by QA Agent · Release Agent Fabric (HTTP smoke — Playwright browser suite can attach later).</em></p>
</body></html>"""


async def upload_qa_report_html(
    *,
    title: str,
    html_body: str,
) -> str:
    """Upload QA report under Documents/QAReports. Returns webUrl or empty."""
    if not ms_graph.graph_configured():
        return ""
    site_id = ""
    from agent.config import settings

    site_id = (settings.ms_graph_sharepoint_site_id or "").strip()
    if not site_id:
        host = (settings.ms_graph_sharepoint_hostname or "").strip()
        path = (settings.ms_graph_sharepoint_site_path or "").strip()
        if host and path:
            if not path.startswith("/"):
                path = "/" + path
            site = await ms_graph.graph_request("GET", f"/sites/{host}:{path}")
            site_id = site["id"]
    if not site_id:
        return ""

    try:
        await ms_graph.graph_request(
            "POST",
            f"/sites/{site_id}/drive/root/children",
            json_body={
                "name": "QAReports",
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
        )
    except Exception:
        logger.info("QAReports folder may already exist")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in title)[:50].strip("-") or "qa-report"
    filename = f"{safe}-{stamp}.html"
    token = await ms_graph.get_app_token()
    path = quote(f"/QAReports/{filename}", safe="/")
    url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:{path}:/content"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "text/html; charset=utf-8"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.put(url, headers=headers, content=html_body.encode("utf-8"))
        if resp.status_code >= 400:
            logger.error("QA report upload failed %s %s", resp.status_code, resp.text[:400])
            return ""
        return resp.json().get("webUrl") or ""
