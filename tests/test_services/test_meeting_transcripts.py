"""Tests for meeting transcript discovery helpers."""

import pytest


def test_is_transcript_file_filters():
    from agent.services import meeting_transcripts as mt

    assert mt._is_transcript_file({"title": "Foo-Meeting Transcript.vtt", "extension": ".vtt"})
    assert not mt._is_transcript_file({"title": "notes.pdf", "extension": ".pdf"})
    assert not mt._is_transcript_file({"title": "random.vtt", "extension": ".vtt"})


def test_list_meetings_filters_vtt_only():
    from agent.services import meeting_transcripts as mt

    rows = [
        {"title": "A-Meeting Transcript.vtt", "extension": ".vtt", "drive_id": "d", "item_id": "1"},
        {"title": "video.mp4", "extension": ".mp4", "drive_id": "d", "item_id": "2"},
    ]
    filtered = [r for r in rows if mt._is_transcript_file(r)]
    assert len(filtered) == 1


@pytest.mark.asyncio
async def test_metadata_extract_mock_llm(monkeypatch):
    from agent.services import meeting_transcripts as mt

    class FakeResp:
        content = (
            '{"client_name":"Global Voyager Inc.","agenda_summary":"Travel planner discovery",'
            '"participants":["Sunny Kushwaha","Priya Mehta"],"meeting_date_guess":"2026-08-22"}'
        )

    async def fake_llm(*args, **kwargs):
        return FakeResp()

    monkeypatch.setattr("agent.services.meeting_transcripts.invoke_llm", fake_llm)
    data = await mt._extract_metadata_llm(
        filename="Travel.vtt",
        excerpt="Global Voyager travel planner AI agent discovery",
        participants_hint=["Sunny Kushwaha"],
    )
    assert data["client_name"] == "Global Voyager Inc."
    assert "Travel" in data["agenda_summary"]


@pytest.mark.asyncio
async def test_publish_upload_mock_graph(monkeypatch):
    from agent.services import meeting_publish

    async def fake_upload(**kwargs):
        return {"web_url": f"https://sp.example/{kwargs['filename']}"}

    monkeypatch.setattr(meeting_publish.graph_docs, "upload_site_drive_item", fake_upload)
    plan = {"title": "Test Plan", "executive_summary": "Summary"}
    md = "# Test Plan\n\nBody"
    out = await meeting_publish.publish_plan(plan, md)
    assert out["md_url"].endswith(".md")
