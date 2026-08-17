"""Ingest + retrieve orchestration for Document Knowledge Fabric.

Vectors prefer Qdrant Cloud when configured; SQLite keeps catalog + chunk text
and remains the offline cosine fallback.

Retrieval quality stack:
  1) text-embedding-3-large (Foundry)
  2) structure-aware chunking + metadata-prefixed embeddings
  3) multi-query expansion + over-fetch + MMR diversify
  4) gpt-4.1 / gpt-5 RAG role for detailed grounded answers
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.config import settings
from agent.core.logging import get_logger
from agent.models import knowledge_doc as kd
from agent.services import doc_vector, graph_docs, qdrant_store
from agent.services.llm import invoke_llm

logger = get_logger(__name__)

_RAG_SYSTEM = """You are the Doc RAG Agent for Sunny's Personal AI Agent.

Grounding rules (strict):
- Answer ONLY from the provided document context blocks. Do not invent facts.
- Every context block is labeled [n] with a retrieval locator (section/page/slide when known).
- Cite claims with [n] matching those blocks. Do NOT invent page numbers.
- If a locator is present (e.g. "§ 2.1 Two-Layer Agent Architecture" or "Page 3"), you may mention it,
  but only if it appears in that block's header.
- If evidence is partial, say what is known vs unknown.

Answer style (detailed + accurate):
1) Direct answer (2–4 sentences)
2) Supporting details (bullets; include numbers/dates/names when present)
3) Caveats / gaps (if any)
4) Sources used ([n] title — locator)

Keep Teams-readable markdown. Be thorough when the context supports it."""


async def ingest_catalog_entries(
    entries: list[dict[str, Any]],
    *,
    owner_session: str | None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for entry in entries:
        external_id = entry.get("external_id") or entry.get("item_id") or ""
        source = entry.get("source_type") or "sharepoint"
        title = entry.get("title") or "untitled"
        doc_mode = entry.get("doc_mode") or graph_docs.infer_doc_mode(title)
        try:
            doc = await kd.upsert_document(
                external_id=external_id,
                source_type=source,
                title=title,
                web_url=entry.get("web_url"),
                mime_type=entry.get("mime_type"),
                doc_mode=doc_mode,
                status=kd.STATUS_INGESTING,
                owner_session=owner_session,
                metadata={
                    "folder": entry.get("folder") or "",
                    "extension": entry.get("extension") or "",
                    "size": entry.get("size") or 0,
                    "last_modified": entry.get("last_modified") or "",
                    "pick": entry.get("pick"),
                },
            )
            text, extract_status = await graph_docs.fetch_document_text(entry)
            bad_status = (
                not text.strip()
                or extract_status.startswith("rejected_")
                or extract_status.startswith("unsupported_")
                or extract_status.endswith("_empty")
                or extract_status.startswith("extract_error_")
                or extract_status in ("empty", "missing_item_id", "missing_onenote_id")
            )
            # Legacy OLE notice is informative but not useful RAG corpus
            if extract_status == "unsupported_legacy_ole":
                bad_status = True
            if bad_status:
                reason = extract_status or "empty"
                await kd.upsert_document(
                    doc_id=doc["id"],
                    external_id=external_id,
                    source_type=source,
                    title=title,
                    status=kd.STATUS_FAILED,
                    extract_status=extract_status or "empty",
                    error=f"No extractable text ({reason})",
                    owner_session=owner_session,
                )
                results.append(
                    {
                        "id": doc["id"],
                        "title": title,
                        "status": "failed",
                        "reason": reason,
                    }
                )
                continue

            records = await doc_vector.build_chunk_records(
                text,
                title=title,
                source_type=source,
                doc_mode=doc_mode,
            )
            vector_backend = "sqlite"
            qdrant_points = 0
            store_local_embeddings = True
            if qdrant_store.qdrant_configured():
                try:
                    qdrant_points = await qdrant_store.upsert_document_chunks(
                        doc_id=doc["id"],
                        title=title,
                        source_type=source,
                        doc_mode=doc_mode,
                        web_url=entry.get("web_url"),
                        owner_session=owner_session,
                        records=records,
                    )
                    if qdrant_points > 0:
                        vector_backend = "qdrant"
                        store_local_embeddings = False
                except Exception:
                    logger.exception(
                        "Qdrant upsert failed for %s — falling back to SQLite vectors",
                        title,
                    )

            sqlite_records = (
                records
                if store_local_embeddings
                else [{"text": r.get("text") or "", "embedding": []} for r in records]
            )
            await kd.replace_chunks(doc["id"], sqlite_records)
            updated = await kd.upsert_document(
                doc_id=doc["id"],
                external_id=external_id,
                source_type=source,
                title=title,
                status=kd.STATUS_READY,
                extract_status=extract_status,
                content_preview=text[:500],
                chunk_count=len(records),
                owner_session=owner_session,
                error="",
                metadata={
                    "folder": entry.get("folder") or "",
                    "extension": entry.get("extension") or "",
                    "size": entry.get("size") or 0,
                    "last_modified": entry.get("last_modified") or "",
                    "pick": entry.get("pick"),
                    "vector_backend": vector_backend,
                    "qdrant_points": qdrant_points,
                    "embedding_deployment": settings.azure_openai_embedding_deployment,
                },
            )
            results.append(
                {
                    "id": updated["id"],
                    "title": title,
                    "status": "ready",
                    "chunks": len(records),
                    "extract_status": extract_status,
                    "doc_mode": updated.get("doc_mode"),
                    "source_type": source,
                    "vector_backend": vector_backend,
                    "qdrant_points": qdrant_points,
                }
            )
        except Exception as exc:
            logger.exception("Ingest failed for %s", title)
            results.append({"title": title, "status": "failed", "reason": str(exc)})
    return results


async def _expand_queries(question: str) -> list[str]:
    """Generate alternate retrieval queries for better recall (multi-query RAG)."""
    q = (question or "").strip()
    if not q:
        return []
    queries = [q]
    if not settings.doc_knowledge_multi_query:
        return queries
    try:
        resp = await invoke_llm(
            [
                SystemMessage(
                    content=(
                        "Generate 2 short alternate search queries for enterprise document retrieval. "
                        "Keep the same intent; vary keywords/synonyms. "
                        'Return JSON only: {"queries":["...","..."]}'
                    )
                ),
                HumanMessage(content=q),
            ],
            temperature=0.2,
            role="rag",
        )
        raw = (getattr(resp, "content", None) or str(resp) or "").strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end] if start != -1 and end > 0 else raw)
        for alt in (data.get("queries") or [])[:2]:
            s = str(alt).strip()
            if s and s.lower() not in {x.lower() for x in queries}:
                queries.append(s)
    except Exception:
        logger.exception("Multi-query expansion failed — using original question only")
        # Lightweight heuristic expansions without LLM
        cleaned = re.sub(r"[^\w\s]", " ", q)
        if cleaned.strip() and cleaned.strip().lower() != q.lower():
            queries.append(cleaned.strip())
    return queries[:3]


async def _search_one(
    query_embedding: list[float],
    *,
    owner_session: str | None,
    doc_ids: list[str] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str]:
    if qdrant_store.qdrant_configured():
        try:
            hits = await qdrant_store.search(
                query_embedding,
                top_k=limit,
                owner_session=None,
                doc_ids=doc_ids,
            )
            if not hits and owner_session:
                hits = await qdrant_store.search(
                    query_embedding,
                    top_k=limit,
                    owner_session=owner_session,
                    doc_ids=doc_ids,
                )
            if hits:
                return hits, "qdrant"
        except Exception:
            logger.exception("Qdrant search failed — using SQLite fallback")

    corpus = await kd.load_chunks_with_embeddings(
        owner_session=owner_session,
        doc_ids=doc_ids,
    )
    if not corpus and owner_session:
        corpus = await kd.load_chunks_with_embeddings(doc_ids=doc_ids)
    if not corpus:
        return [], "none"
    with_vectors = [c for c in corpus if c.get("embedding")]
    if not with_vectors:
        return [], "sqlite_no_vectors"
    hits = doc_vector.search_chunks(query_embedding, with_vectors, top_k=limit)
    return hits, "sqlite"


async def _retrieve_hits(
    question: str,
    *,
    owner_session: str | None = None,
    doc_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Multi-query retrieve → merge → MMR diversify."""
    queries = await _expand_queries(question)
    fetch_k = max(
        int(settings.doc_knowledge_top_k or 8),
        int(settings.doc_knowledge_fetch_k or 24),
    )
    top_k = int(settings.doc_knowledge_top_k or 8)
    merged: dict[str, dict[str, Any]] = {}
    backend = "none"

    embeddings = await doc_vector.embed_texts(queries)
    for emb in embeddings:
        hits, backend = await _search_one(
            emb,
            owner_session=owner_session,
            doc_ids=doc_ids,
            limit=fetch_k,
        )
        for h in hits:
            key = f"{h.get('doc_id')}:{h.get('chunk_index')}:{hash((h.get('text') or '')[:120])}"
            prev = merged.get(key)
            if prev is None or float(h.get("score") or 0) > float(prev.get("score") or 0):
                merged[key] = h

    if not merged:
        return [], backend

    ranked = sorted(merged.values(), key=lambda r: float(r.get("score") or 0), reverse=True)
    diversified = doc_vector.mmr_select(ranked[:fetch_k], top_k=top_k)
    return diversified, backend


async def retrieve_answer(
    question: str,
    *,
    owner_session: str | None = None,
    doc_ids: list[str] | None = None,
) -> dict[str, Any]:
    ready = await kd.list_ready_documents(owner_session=None, limit=1)
    if not ready and owner_session:
        ready = await kd.list_ready_documents(owner_session=owner_session, limit=1)
    if not ready:
        return {
            "answer": (
                "No ingested documents yet. Say *list my documents*, pick numbers, "
                "then *ingest 1,2* before asking."
            ),
            "citations": [],
            "hits": [],
            "vector_backend": "none",
        }

    hits, backend = await _retrieve_hits(
        question, owner_session=owner_session, doc_ids=doc_ids
    )
    if not hits:
        return {
            "answer": (
                "Documents are ingested but no matching chunks were retrieved. "
                "If you just enabled Qdrant or switched embedding models, re-run *ingest* "
                f"so vectors are upserted. (backend={backend})"
            ),
            "citations": [],
            "hits": [],
            "vector_backend": backend,
        }

    min_score = float(settings.doc_knowledge_min_score or 0.0)
    # Qdrant cosine scores are typically higher; apply softer floor there
    floor = min_score if backend == "sqlite" else max(0.0, min_score * 0.5)
    filtered = [h for h in hits if float(h.get("score") or 0) >= floor] or hits[:4]

    context_blocks = []
    citations = []
    for i, h in enumerate(filtered, start=1):
        locator = (h.get("locator") or "").strip()
        if not locator:
            # Derive on the fly for older Qdrant points ingested before locator payload
            from agent.services.doc_vector import extract_chunk_locator

            loc = extract_chunk_locator(h.get("text") or "")
            locator = loc.get("locator") or ""
            if not h.get("page"):
                h["page"] = loc.get("page")
            if not h.get("slide"):
                h["slide"] = loc.get("slide")
            if not h.get("section"):
                h["section"] = loc.get("section") or ""
            h["locator"] = locator

        loc_hdr = f" § {locator}" if locator else ""
        context_blocks.append(
            f"[{i}] {h.get('title')}{loc_hdr} "
            f"({h.get('source_type')}/{h.get('doc_mode')})\n"
            f"{h.get('text')}"
        )
        citations.append(
            {
                "n": i,
                "title": h.get("title"),
                "source_type": h.get("source_type"),
                "doc_mode": h.get("doc_mode"),
                "web_url": h.get("web_url"),
                "score": round(float(h.get("score") or 0), 4),
                "doc_id": h.get("doc_id"),
                "chunk_index": h.get("chunk_index"),
                "locator": locator,
                "section": h.get("section") or "",
                "page": h.get("page"),
                "slide": h.get("slide"),
            }
        )
    context = "\n\n---\n\n".join(context_blocks)
    messages = [
        SystemMessage(content=_RAG_SYSTEM),
        HumanMessage(
            content=(
                f"Question:\n{question}\n\n"
                f"Document context:\n{context}\n\n"
                "Write a detailed, citation-grounded answer."
            )
        ),
    ]
    resp = await invoke_llm(messages, temperature=0.15, role="rag")
    answer = (getattr(resp, "content", None) or str(resp) or "").strip()
    related = await suggest_related_queries(question, citations, answer=answer)
    return {
        "answer": answer,
        "citations": citations,
        "hits": filtered,
        "vector_backend": backend,
        "related_queries": related,
    }


async def suggest_related_queries(
    question: str,
    citations: list[dict[str, Any]],
    *,
    answer: str = "",
    limit: int = 3,
) -> list[str]:
    """Suggest follow-up prompts grounded in retrieved locators (no invented docs)."""
    locs: list[str] = []
    titles: list[str] = []
    for c in citations[:8]:
        loc = (c.get("locator") or c.get("section") or "").strip()
        title = (c.get("title") or "").strip()
        if loc and loc not in locs:
            locs.append(loc)
        if title and title not in titles:
            titles.append(title)
    if not locs and not titles:
        return [
            "ask docs summarize the key architecture patterns",
            "list ingested documents",
            "summarize docs risks and recommended actions",
        ][:limit]

    # Deterministic, citation-grounded suggestions (safe for enterprise — no LLM drift)
    out: list[str] = []
    for loc in locs[:4]:
        # Strip page/slide prefixes for cleaner prompts
        clean = re.sub(r"^(Page|Slide)\s+\d+\s*[·\-:]?\s*", "", loc).strip() or loc
        q = f"ask docs what does this document say about {clean}?"
        if q.lower() not in {question.lower(), *(x.lower() for x in out)}:
            out.append(q)
        if len(out) >= limit:
            break
    if len(out) < limit and titles:
        out.append(f"ask docs summarize key points from {titles[0]}")
    if len(out) < limit:
        out.append("summarize docs risks and recommended actions")
    # Optional LLM polish constrained to locator list only
    if locs and settings.doc_knowledge_multi_query:
        try:
            resp = await invoke_llm(
                [
                    SystemMessage(
                        content=(
                            "Propose up to 3 short follow-up questions a user can ask next. "
                            "Each MUST start with 'ask docs ' or 'summarize docs '. "
                            "Use ONLY these section titles (do not invent topics):\n"
                            + "\n".join(f"- {x}" for x in locs[:6])
                            + '\nReturn JSON: {"queries":["..."]}'
                        )
                    ),
                    HumanMessage(
                        content=f"User question: {question}\nAnswer excerpt: {(answer or '')[:400]}"
                    ),
                ],
                temperature=0.2,
                role="rag",
            )
            raw = (getattr(resp, "content", None) or str(resp) or "").strip()
            start, end = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[start:end] if start != -1 and end > 0 else raw)
            polished: list[str] = []
            for q in (data.get("queries") or [])[:limit]:
                s = str(q).strip()
                if not s.lower().startswith(("ask docs", "summarize docs", "ask knowledge")):
                    s = f"ask docs {s}"
                if s.lower() != question.lower() and s not in polished:
                    polished.append(s)
            if polished:
                return polished[:limit]
        except Exception:
            logger.exception("Related-query polish failed — using deterministic suggestions")
    return out[:limit]


async def summarize_insights(
    focus: str,
    *,
    owner_session: str | None = None,
    doc_ids: list[str] | None = None,
) -> dict[str, Any]:
    docs = await kd.list_ready_documents(owner_session=owner_session, limit=40)
    if not docs:
        docs = await kd.list_ready_documents(limit=40)
    if doc_ids:
        docs = [d for d in docs if d["id"] in set(doc_ids)]
    if not docs:
        return {
            "summary": "No ready ingested documents. Ingest first via *list my documents* → *ingest N*.",
            "docs_used": [],
        }

    if focus and len(focus.split()) >= 3:
        rag = await retrieve_answer(focus, owner_session=owner_session, doc_ids=doc_ids)
        context = "\n\n".join(
            f"- {c['title']} ({c['source_type']}/{c['doc_mode']}): score={c['score']}"
            for c in rag.get("citations") or []
        )
        evidence = "\n\n".join(
            f"### {h.get('title')}\n{h.get('text')}" for h in (rag.get("hits") or [])[:10]
        )
    else:
        context = "\n".join(
            f"- {d['title']} [{d['source_type']}/{d['doc_mode']}] chunks={d['chunk_count']}"
            for d in docs[:20]
        )
        evidence = "\n\n".join(
            f"### {d['title']}\n{(d.get('content_preview') or '')[:800]}" for d in docs[:12]
        )

    system = (
        "You are the Doc Insights Agent. Produce executive insights from ingested enterprise docs. "
        "Structure:\n"
        "1) Executive summary\n"
        "2) Key themes (detailed)\n"
        "3) Risks / gaps\n"
        "4) Recommended actions\n"
        "5) Open questions\n"
        "Teams-friendly markdown. Do not invent facts beyond the evidence. Cite document titles."
    )
    prompt = (
        f"Focus: {focus or 'overall corpus health'}\n\n"
        f"Corpus:\n{context}\n\nEvidence excerpts:\n{evidence}"
    )
    resp = await invoke_llm(
        [SystemMessage(content=system), HumanMessage(content=prompt)],
        temperature=0.2,
        role="rag",
    )
    summary = (getattr(resp, "content", None) or str(resp) or "").strip()
    return {
        "summary": summary,
        "docs_used": [
            {
                "id": d["id"],
                "title": d["title"],
                "source_type": d["source_type"],
                "doc_mode": d["doc_mode"],
            }
            for d in docs[:20]
        ],
    }
