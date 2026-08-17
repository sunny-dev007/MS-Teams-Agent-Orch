"""Chunking, embeddings, and cosine retrieval for Document Knowledge Fabric.

Uses Azure OpenAI embeddings (prefer text-embedding-3-large) via httpx.
Structure-aware recursive chunking with overlap + optional title context prefix.
Falls back to a deterministic hash embedding when the deployment is unavailable
(tests / local without embed model) so the pipeline still exercises end-to-end.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

import httpx

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

# Split priority: markdown/HTML-ish headings → paragraphs → sentences → hard cut
_HEADING_SPLIT = re.compile(
    r"(?m)(?=^#{1,6}\s+\S)|(?=^(?:[A-Z][A-Za-z0-9 /&_-]{2,80})\n[=-]{3,}\s*$)"
    r"|(?=^\d+(?:\.\d+)*\.\s+\S)|(?=^[A-Z][A-Z0-9 /&_-]{4,}\n)"
)
_PARA_SPLIT = re.compile(r"\n\s*\n+")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def _soft_split(text: str, pattern: re.Pattern[str]) -> list[str]:
    parts = [p.strip() for p in pattern.split(text) if p and p.strip()]
    return parts if parts else ([text.strip()] if text.strip() else [])


def _hard_windows(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        # Prefer breaking at whitespace near the end
        if end < len(text):
            cut = text.rfind(" ", start + size // 2, end)
            if cut > start:
                end = cut
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def _pack_units(units: list[str], size: int, overlap: int) -> list[str]:
    """Greedy pack semantic units into size-bounded chunks with tail overlap."""
    if not units:
        return []
    chunks: list[str] = []
    buf = ""
    for unit in units:
        if len(unit) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_hard_windows(unit, size, overlap))
            continue
        candidate = f"{buf}\n\n{unit}".strip() if buf else unit
        if len(candidate) <= size:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            # Carry overlap from previous buffer into next
            if overlap > 0 and len(buf) > overlap:
                tail = buf[-overlap:].lstrip()
                buf = f"{tail}\n\n{unit}".strip() if tail else unit
                if len(buf) > size:
                    chunks.append(unit)
                    buf = ""
            else:
                buf = unit
        else:
            buf = unit
    if buf:
        chunks.append(buf)
    return chunks


def chunk_text(
    text: str,
    *,
    chunk_chars: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """Structure-aware recursive chunking for RAG quality.

    Order: heading sections → paragraphs → sentences → character windows.
    Overlap preserves boundary context across adjacent chunks.
    """
    body = (text or "").strip()
    if not body:
        return []
    size = int(chunk_chars or settings.doc_knowledge_chunk_chars or 1800)
    ov = int(overlap if overlap is not None else settings.doc_knowledge_chunk_overlap or 360)
    ov = max(0, min(ov, size // 2))

    sections = _soft_split(body, _HEADING_SPLIT)
    if len(sections) == 1:
        sections = _soft_split(body, _PARA_SPLIT)

    refined: list[str] = []
    for sec in sections:
        if len(sec) <= size:
            refined.append(sec)
            continue
        paras = _soft_split(sec, _PARA_SPLIT)
        if len(paras) == 1 and len(paras[0]) > size:
            sents = _soft_split(sec, _SENT_SPLIT)
            refined.extend(sents if len(sents) > 1 else [sec])
        else:
            refined.extend(paras)

    packed = _pack_units(refined, size, ov)
    # Deduplicate accidental empties / exact dupes from overlap edges
    out: list[str] = []
    seen: set[str] = set()
    for c in packed:
        key = c.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def with_chunk_context(
    chunks: list[str],
    *,
    title: str = "",
    source_type: str = "",
    doc_mode: str = "",
) -> list[str]:
    """Prefix each chunk with document metadata so embeddings carry provenance."""
    header_bits = []
    if title:
        header_bits.append(f"Document: {title}")
    if source_type:
        header_bits.append(f"Source: {source_type}")
    if doc_mode:
        header_bits.append(f"Mode: {doc_mode}")
    if not header_bits:
        return chunks
    header = " | ".join(header_bits)
    return [f"{header}\n\n{c}" for c in chunks]


_PAGE_RE = re.compile(r"(?im)^\s*##\s*Page\s+(\d+)\b")
_SLIDE_RE = re.compile(r"(?im)^\s*##\s*Slide\s+(\d+)\b")
_MD_HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
_NUMBERED_SECTION_RE = re.compile(r"(?m)^(\d+(?:\.\d+){0,3})\s+([A-Z][^\n]{2,80})\s*$")


def extract_chunk_locator(text: str) -> dict[str, Any]:
    """Derive human citation locator from chunk text (page/slide/section).

    Best-practice RAG citations should point to a retrieval-native location,
    not invent page numbers. We only emit what the chunk itself contains.
    """
    body = text or ""
    page = None
    slide = None
    section = ""

    m_page = _PAGE_RE.search(body)
    if m_page:
        page = int(m_page.group(1))
        section = f"Page {page}"

    m_slide = _SLIDE_RE.search(body)
    if m_slide:
        slide = int(m_slide.group(1))
        if not section:
            section = f"Slide {slide}"

    # Prefer markdown headings inside the chunk (first heading wins for locator)
    headings = _MD_HEADING_RE.findall(body)
    if headings:
        # Use deepest/most specific heading near the top; prefer ## / ### over #
        headings_sorted = sorted(headings, key=lambda h: (-len(h[0]), 0))
        title = headings_sorted[0][1].strip()
        # Skip generic Page/Slide headings we already captured
        if not re.match(r"(?i)^page\s+\d+$", title) and not re.match(r"(?i)^slide\s+\d+$", title):
            section = title if not section else f"{section} · {title}"

    if not section:
        m_num = _NUMBERED_SECTION_RE.search(body)
        if m_num:
            section = f"{m_num.group(1)} {m_num.group(2).strip()}"

    locator = section or ""
    if page is not None and "Page" not in locator:
        locator = f"Page {page}" + (f" · {section}" if section and section != f"Page {page}" else "")
    if slide is not None and "Slide" not in locator:
        locator = f"Slide {slide}" + (f" · {section}" if section and section != f"Slide {slide}" else "")

    return {
        "page": page,
        "slide": slide,
        "section": section,
        "locator": locator.strip(" ·"),
    }


def _hash_embedding(text: str, dims: int = 64) -> list[float]:
    """Deterministic pseudo-embedding for offline/tests — not for production quality."""
    vec = [0.0] * dims
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    if not tokens:
        tokens = ["empty"]
    for tok in tokens:
        digest = hashlib.sha256(tok.encode()).digest()
        for i in range(dims):
            vec[i] += (digest[i % len(digest)] / 255.0) - 0.5
    return _l2_normalize(vec)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


def mmr_select(
    candidates: list[dict[str, Any]],
    *,
    top_k: int,
    lambda_mult: float = 0.7,
) -> list[dict[str, Any]]:
    """Maximal Marginal Relevance — balance relevance vs diversity across docs/chunks."""
    if not candidates or top_k <= 0:
        return []
    remaining = list(candidates)
    selected: list[dict[str, Any]] = []

    def _sim(a: dict[str, Any], b: dict[str, Any]) -> float:
        ea, eb = a.get("embedding") or [], b.get("embedding") or []
        if ea and eb and len(ea) == len(eb):
            return cosine_similarity(ea, eb)
        # Fallback: same doc / overlapping text heuristic
        if a.get("doc_id") and a.get("doc_id") == b.get("doc_id"):
            return 0.55
        ta = (a.get("text") or "")[:200]
        tb = (b.get("text") or "")[:200]
        if not ta or not tb:
            return 0.0
        sa, sb = set(ta.lower().split()), set(tb.lower().split())
        return len(sa & sb) / max(1, len(sa | sb))

    while remaining and len(selected) < top_k:
        best_i = 0
        best_score = -1e9
        for i, cand in enumerate(remaining):
            rel = float(cand.get("score") or 0.0)
            if not selected:
                mmr = rel
            else:
                div = max(_sim(cand, s) for s in selected)
                mmr = lambda_mult * rel - (1.0 - lambda_mult) * div
            if mmr > best_score:
                best_score = mmr
                best_i = i
        selected.append(remaining.pop(best_i))
    return selected


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings via Azure OpenAI embeddings API."""
    if not texts:
        return []
    endpoint = (settings.azure_openai_endpoint or "").rstrip("/")
    key = settings.effective_api_key
    deployment = (settings.azure_openai_embedding_deployment or "").strip()
    if not endpoint or not key or not deployment:
        logger.warning("Embedding deployment unavailable — using hash embeddings")
        return [_hash_embedding(t) for t in texts]

    url = (
        f"{endpoint}/openai/deployments/{deployment}/embeddings"
        f"?api-version={settings.azure_openai_api_version}"
    )
    headers = {"api-key": key, "Content-Type": "application/json"}
    # Batch in slices to stay under request limits for large docs
    batch_size = 16
    all_vectors: list[list[float]] = []
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                resp = await client.post(url, headers=headers, json={"input": batch})
                if resp.status_code >= 400:
                    logger.error("Embeddings HTTP %s %s", resp.status_code, resp.text[:400])
                    resp.raise_for_status()
                data = resp.json().get("data") or []
                data = sorted(data, key=lambda d: int(d.get("index", 0)))
                vectors = [
                    _l2_normalize([float(x) for x in (d.get("embedding") or [])]) for d in data
                ]
                if len(vectors) != len(batch):
                    raise RuntimeError("Embedding count mismatch")
                all_vectors.extend(vectors)
        return all_vectors
    except Exception:
        logger.exception("Azure embeddings failed — falling back to hash embeddings")
        return [_hash_embedding(t) for t in texts]


async def build_chunk_records(
    text: str,
    *,
    title: str = "",
    source_type: str = "",
    doc_mode: str = "",
) -> list[dict[str, Any]]:
    pieces = chunk_text(text)
    if not pieces:
        return []
    embed_inputs = with_chunk_context(
        pieces, title=title, source_type=source_type, doc_mode=doc_mode
    )
    vectors = await embed_texts(embed_inputs)
    # Store raw chunk text for citations; embedding used contextualized text
    out: list[dict[str, Any]] = []
    for t, v, et in zip(pieces, vectors, embed_inputs, strict=True):
        loc = extract_chunk_locator(t)
        out.append(
            {
                "text": t,
                "embedding": v,
                "embed_text": et,
                "locator": loc.get("locator") or "",
                "page": loc.get("page"),
                "slide": loc.get("slide"),
                "section": loc.get("section") or "",
            }
        )
    return out


def search_chunks(
    query_embedding: list[float],
    corpus: list[dict[str, Any]],
    *,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    k = int(top_k or settings.doc_knowledge_top_k or 8)
    scored: list[dict[str, Any]] = []
    for row in corpus:
        emb = row.get("embedding") or []
        score = cosine_similarity(query_embedding, emb)
        scored.append({**row, "score": score})
    scored.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
    fetch_k = max(k, int(settings.doc_knowledge_fetch_k or k))
    return mmr_select(scored[:fetch_k], top_k=k)
