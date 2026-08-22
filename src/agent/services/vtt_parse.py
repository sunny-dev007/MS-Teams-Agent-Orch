"""WebVTT parser for Teams meeting transcripts.

Feature: Meeting Intelligence Fabric — isolated from calendar scheduling agent.
"""

from __future__ import annotations

import re
from typing import Any

_SPEAKER_RE = re.compile(r"^<v\s+([^>]+)>(.*)$", re.I | re.DOTALL)
_TIME_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\s*$"
)
_FILENAME_DATE_RE = re.compile(r"(\d{8})[_-](\d{6})")


def _decode(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8-sig", errors="replace")
    return str(raw or "")


def _time_to_seconds(ts: str) -> float:
    parts = (ts or "").strip().split(":")
    if len(parts) != 3:
        return 0.0
    try:
        h, m, rest = parts
        s, _, ms = rest.partition(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms or 0) / 1000.0
    except ValueError:
        return 0.0


def parse_vtt(raw: bytes | str) -> list[dict[str, Any]]:
    """Parse WebVTT into cue dicts: start, end, speaker, text."""
    text = _decode(raw)
    lines = text.splitlines()
    cues: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.upper().startswith("WEBVTT") or line.startswith("NOTE"):
            i += 1
            continue
        m = _TIME_RE.match(line)
        if not m:
            i += 1
            continue
        start, end = m.group(1), m.group(2)
        i += 1
        body_lines: list[str] = []
        while i < len(lines) and lines[i].strip() and not _TIME_RE.match(lines[i].strip()):
            body_lines.append(lines[i].strip())
            i += 1
        body = " ".join(body_lines).strip()
        speaker = ""
        spoken = body
        sm = _SPEAKER_RE.match(body)
        if sm:
            speaker = sm.group(1).strip()
            spoken = sm.group(2).strip()
        if spoken:
            cues.append(
                {
                    "start": start,
                    "end": end,
                    "start_sec": _time_to_seconds(start),
                    "end_sec": _time_to_seconds(end),
                    "speaker": speaker,
                    "text": spoken,
                }
            )
    return cues


def vtt_to_plain_text(cues: list[dict[str, Any]]) -> str:
    """Speaker-labeled prose suitable for LLM prompts."""
    parts: list[str] = []
    last_speaker = ""
    for cue in cues:
        speaker = (cue.get("speaker") or "").strip()
        text = (cue.get("text") or "").strip()
        if not text:
            continue
        if speaker and speaker != last_speaker:
            parts.append(f"{speaker}: {text}")
            last_speaker = speaker
        elif speaker:
            parts.append(text)
        else:
            parts.append(text)
    return "\n\n".join(parts)


def vtt_metadata_heuristic(filename: str) -> dict[str, Any]:
    """Extract meeting date/time hints from Teams filename patterns."""
    name = (filename or "").strip()
    out: dict[str, Any] = {"meeting_date": None, "duration_seconds": None}
    m = _FILENAME_DATE_RE.search(name)
    if m:
        ymd, hms = m.group(1), m.group(2)
        try:
            out["meeting_date"] = (
                f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}T{hms[0:2]}:{hms[2:4]}:{hms[4:6]}"
            )
        except (IndexError, ValueError):
            pass
    return out


def duration_from_cues(cues: list[dict[str, Any]]) -> int | None:
    if not cues:
        return None
    try:
        end = max(float(c.get("end_sec") or 0) for c in cues)
        return max(1, int(end // 60) * 60 + int(end % 60)) if end > 0 else None
    except (TypeError, ValueError):
        return None


def participants_from_cues(cues: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for cue in cues:
        sp = (cue.get("speaker") or "").strip()
        if sp and sp.lower() not in seen:
            seen.add(sp.lower())
            out.append(sp)
    return out
