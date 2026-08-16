"""Tests for QA HTTP smoke runner."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.services.qa_smoke import render_qa_report_html, run_http_smoke


@pytest.mark.asyncio
async def test_http_smoke_all_pass():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"healthy"}'

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("agent.services.qa_smoke.httpx.AsyncClient", return_value=mock_client):
        report = await run_http_smoke("https://example.azurewebsites.net")

    assert report["overall"] == "passed"
    assert report["failed"] == 0
    assert report["total"] == 4


@pytest.mark.asyncio
async def test_http_smoke_detects_failure():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "error"

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("agent.services.qa_smoke.httpx.AsyncClient", return_value=mock_client):
        report = await run_http_smoke("https://example.azurewebsites.net")

    assert report["overall"] == "failed"
    assert report["failed"] >= 1


def test_render_qa_report_html():
    report = {
        "overall": "passed",
        "passed": 2,
        "failed": 0,
        "total": 2,
        "app_url": "https://example.com",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "runner": "http_smoke_v1",
        "checks": [
            {
                "name": "health",
                "url": "https://example.com/health",
                "status_code": 200,
                "passed": True,
                "elapsed_ms": 12,
                "snippet": "ok",
            }
        ],
    }
    html = render_qa_report_html(report, release_id="rel_x", pr_id="41")
    assert "QA Smoke Report" in html
    assert "PASSED" in html
    assert "rel_x" in html
