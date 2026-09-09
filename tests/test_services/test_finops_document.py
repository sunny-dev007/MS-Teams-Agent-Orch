"""Orbit FinOps DOCX path — isolation from Copilot Studio Doc Author."""

from __future__ import annotations

import pytest

from agent.services import document_author
from agent.services import finops_document as fd
from agent.services.llm import deployment_chain


def test_wants_finops_cost_report_detects_optimisation_docx():
    assert fd.wants_finops_cost_report(
        "prepare the document as docx beautify recommendations about this azure",
        session_data={
            "azure_finops_sub": {"id": "sub-1", "name": "Pay-As-You-Go"},
            "azure_finops_last_costs_svc": [{"name": "App Service", "cost": 100}],
        },
    )
    assert not fd.wants_finops_cost_report(
        "write a document about our hiring process",
        session_data={},
    )


def test_build_analysis_bundle_ranks_cost_over_free_sku():
    bundle = fd.build_analysis_bundle(
        sub={"id": "s1", "name": "Pay-As-You-Go"},
        cost_by_rg=[
            {"name": "feapp", "cost": 3306.18, "currency": "INR"},
            {"name": "feappdb", "cost": 401.27, "currency": "INR"},
            {"name": "rg-todoapp-poc", "cost": 72.26, "currency": "INR"},
        ],
        cost_by_svc=[
            {"name": "Azure App Service", "cost": 4006.84, "currency": "INR"},
            {"name": "SQL Database", "cost": 1388.01, "currency": "INR"},
        ],
        cost_by_res=[
            {
                "name": "/subscriptions/x/resourceGroups/feapp/providers/Microsoft.Web/serverfarms/ASP-FEApp-a9ed",
                "cost": 3306.18,
                "currency": "INR",
            }
        ],
        resources=[
            {
                "name": "WestUS2LinuxDynamicPlan",
                "type": "Microsoft.Web/serverfarms",
                "sku": "Y1",
                "rg": "other",
            },
            {
                "name": "ASP-FEApp-a9ed",
                "type": "Microsoft.Web/serverfarms",
                "sku": "F1",
                "rg": "feapp",
            },
        ],
        recs=[
            {
                "severity": "high",
                "title": "Review WestUS2LinuxDynamicPlan",
                "detail": "Y1 plan",
            }
        ],
        days=30,
    )
    assert bundle["total"] > 4000
    assert bundle["cost_rank"][0]["target"] == "feapp"
    assert bundle["cost_coverage"] > 0.5
    md = fd.build_markdown(bundle, narrative="feapp drives the bill.")
    assert "Cost Optimisation Recommendations" in md
    assert "feapp" in md
    assert "3306.18" in md or "3,306.18" in md
    charts = fd.render_charts(bundle)
    assert isinstance(charts, dict)


def test_orbit_vs_copilot_model_isolation():
    from agent.services.orbit_turn import channel_surface_scope

    with channel_surface_scope("copilot"):
        copilot_chat = deployment_chain(role="default")
        copilot_doc = deployment_chain(role="doc_author")
    with channel_surface_scope("orbit"):
        orbit_chat = deployment_chain(role="default")
        orbit_doc = deployment_chain(role="doc_author")

    # AI Dev Agent / Copilot keeps shared baseline
    assert copilot_chat[0] == "gpt-4o-mini"
    assert copilot_doc[0] == "gpt-4o-mini"
    # Orbit upgrades only
    assert orbit_chat[0] == "gpt-4o"
    assert orbit_doc[0] == "gpt-4.1"



@pytest.mark.asyncio
async def test_create_sharepoint_skips_finops_pack_for_copilot(monkeypatch):
    """AI Dev Agent / Copilot surface must not enter Orbit FinOps DOCX path."""
    called = {"finops": False}

    async def _boom(*a, **k):
        called["finops"] = True
        raise AssertionError("Orbit FinOps path must not run for copilot")

    monkeypatch.setattr(
        "agent.services.finops_document.gather_finops_bundle", _boom
    )

    async def _fake_md(topic, conversation_context=""):
        return "Generic Title", "# Generic Title\n\nHello"

    async def _fake_pub(title, markdown, folder=None):
        return {
            "title": title,
            "folder": "Documents/Generated",
            "md_url": "https://example/md",
            "docx_url": "https://example/docx",
            "md_filename": "a.md",
            "docx_filename": "a.docx",
            "created_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(document_author, "generate_markdown", _fake_md)
    monkeypatch.setattr(document_author, "publish_document", _fake_pub)
    monkeypatch.setattr(
        document_author.ms_graph, "doc_knowledge_ready", lambda: True
    )

    result = await document_author.create_sharepoint_document(
        "prepare docx about azure cost optimisation",
        session_data={
            "channel_surface": "copilot",
            "azure_finops_sub": {"id": "s", "name": "Pay-As-You-Go"},
        },
        channel_surface="copilot",
    )
    assert called["finops"] is False
    assert result["title"] == "Generic Title"


@pytest.mark.asyncio
async def test_orbit_surface_enters_finops_pack(monkeypatch):
    async def _bundle(session_data):
        return fd.build_analysis_bundle(
            sub={"id": "s1", "name": "Pay-As-You-Go"},
            cost_by_rg=[{"name": "feapp", "cost": 100.0, "currency": "INR"}],
            cost_by_svc=[{"name": "App Service", "cost": 100.0, "currency": "INR"}],
            cost_by_res=[],
            resources=[],
            recs=[],
            days=30,
        )

    async def _narr(bundle, msg):
        return "Narrative for feapp."

    uploads: list[str] = []

    async def _upload(*, folder, filename, content, content_type):
        uploads.append(filename)
        return {"web_url": f"https://example/{filename}"}

    monkeypatch.setattr(document_author.settings, "enable_azure_finops_agent", True)
    monkeypatch.setattr(document_author, "azure_finops_ready_safe", lambda: True)
    monkeypatch.setattr(
        "agent.services.finops_document.gather_finops_bundle", _bundle
    )
    monkeypatch.setattr(
        "agent.services.finops_document.polish_narrative", _narr
    )
    monkeypatch.setattr(
        "agent.services.finops_document.render_charts", lambda b: {}
    )
    monkeypatch.setattr(
        "agent.services.finops_document.render_finops_docx",
        lambda b, m, c: b"%PDF-fake-docx",
    )
    monkeypatch.setattr(
        document_author.ms_graph, "doc_knowledge_ready", lambda: True
    )
    monkeypatch.setattr(
        document_author.graph_docs, "upload_site_drive_item", _upload
    )

    result = await document_author.create_sharepoint_document(
        "prepare docx with azure cost recommendations",
        session_data={"channel_surface": "orbit", "azure_finops_sub": {"id": "s1"}},
        channel_surface="orbit",
    )
    assert result["report_kind"] == "orbit_finops_cost"
    assert result["title"] == "Cost Optimisation Recommendations"
    assert any(name.endswith(".docx") for name in uploads)
