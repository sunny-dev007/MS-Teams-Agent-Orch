"""Tests for WebVTT parsing."""

from pathlib import Path

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "samples"
    / "meeting-transcript"
    / "Travel Planner AI Agent-20250822_100000-Meeting Transcript.vtt"
)


def test_vtt_parse_sample_file():
    from agent.services import vtt_parse

    raw = SAMPLE.read_bytes()
    cues = vtt_parse.parse_vtt(raw)
    assert len(cues) == 113
    assert any(c.get("speaker") == "Priya Mehta" for c in cues)
    plain = vtt_parse.vtt_to_plain_text(cues)
    assert "Global Voyager" in plain
    meta = vtt_parse.vtt_metadata_heuristic(SAMPLE.name)
    assert meta.get("meeting_date", "").startswith("2025-08-22")


def test_participants_from_cues():
    from agent.services import vtt_parse

    cues = [
        {"speaker": "Alice", "text": "Hi"},
        {"speaker": "Bob", "text": "Hello"},
        {"speaker": "Alice", "text": "Again"},
    ]
    assert vtt_parse.participants_from_cues(cues) == ["Alice", "Bob"]
