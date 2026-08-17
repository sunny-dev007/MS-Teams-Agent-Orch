"""Chunking, embeddings, and cosine retrieval for Document Knowledge Fabric.

Uses Azure OpenAI embeddings deployment via httpx (no new dependencies).
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


def chunk_text(
    text: str,
    *,
    chunk_chars: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    body = (text or "").strip()
    if not body:
        return []
    size = int(chunk_chars or settings.doc_knowledge_chunk_chars or 1200)
    ov = int(overlap if overlap is not None else settings.doc_knowledge_chunk_overlap or 150)
    ov = max(0, min(ov, size // 2))
    # Prefer paragraph boundaries when possible
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paras:
        paras = [body]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= size:
            buf = f"{buf}\n\n{p}".strip() if buf else p
            continue
        if buf:
            chunks.append(buf)
        if len(p) <= size:
            buf = p
        else:
            start = 0
            while start < len(p):
                end = min(len(p), start + size)
                chunks.append(p[start:end])
                if end >= len(p):
                    break
                start = max(end - ov, start + 1)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


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
    # Azure allows batch input
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(url, headers=headers, json={"input": texts})
            if resp.status_code >= 400:
                logger.error("Embeddings HTTP %s %s", resp.status_code, resp.text[:400])
                resp.raise_for_status()
            data = resp.json().get("data") or []
            # Ensure order by index
            data = sorted(data, key=lambda d: int(d.get("index", 0)))
            vectors = [_l2_normalize([float(x) for x in (d.get("embedding") or [])]) for d in data]
            if len(vectors) != len(texts):
                raise RuntimeError("Embedding count mismatch")
            return vectors
    except Exception:
        logger.exception("Azure embeddings failed — falling back to hash embeddings")
        return [_hash_embedding(t) for t in texts]


async def build_chunk_records(text: str) -> list[dict[str, Any]]:
    pieces = chunk_text(text)
    if not pieces:
        return []
    vectors = await embed_texts(pieces)
    return [{"text": t, "embedding": v} for t, v in zip(pieces, vectors, strict=True)]


def search_chunks(
    query_embedding: list[float],
    corpus: list[dict[str, Any]],
    *,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    k = int(top_k or settings.doc_knowledge_top_k or 5)
    scored: list[dict[str, Any]] = []
    for row in corpus:
        emb = row.get("embedding") or []
        score = cosine_similarity(query_embedding, emb)
        scored.append({**row, "score": score})
    scored.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
    return scored[:k]
