"""Ingest + retrieve orchestration for Document Knowledge Fabric."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.config import settings
from agent.core.logging import get_logger
from agent.models import knowledge_doc as kd
from agent.services import doc_vector, graph_docs
from agent.services.llm import invoke_llm

logger = get_logger(__name__)


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
        try:
            doc = await kd.upsert_document(
                external_id=external_id,
                source_type=source,
                title=title,
                web_url=entry.get("web_url"),
                mime_type=entry.get("mime_type"),
                doc_mode=entry.get("doc_mode") or graph_docs.infer_doc_mode(title),
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
            if not text.strip():
                await kd.upsert_document(
                    doc_id=doc["id"],
                    external_id=external_id,
                    source_type=source,
                    title=title,
                    status=kd.STATUS_FAILED,
                    extract_status=extract_status or "empty",
                    error="No extractable text",
                    owner_session=owner_session,
                )
                results.append({"id": doc["id"], "title": title, "status": "failed", "reason": "empty"})
                continue

            records = await doc_vector.build_chunk_records(text)
            await kd.replace_chunks(doc["id"], records)
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
                }
            )
        except Exception as exc:
            logger.exception("Ingest failed for %s", title)
            results.append({"title": title, "status": "failed", "reason": str(exc)})
    return results


async def retrieve_answer(
    question: str,
    *,
    owner_session: str | None = None,
    doc_ids: list[str] | None = None,
) -> dict[str, Any]:
    corpus = await kd.load_chunks_with_embeddings(
        owner_session=owner_session,
        doc_ids=doc_ids,
    )
    if not corpus:
        return {
            "answer": (
                "No ingested documents yet. Say *list my documents*, pick numbers, "
                "then *ingest 1,2* before asking."
            ),
            "citations": [],
            "hits": [],
        }

    q_emb = (await doc_vector.embed_texts([question]))[0]
    hits = doc_vector.search_chunks(q_emb, corpus, top_k=settings.doc_knowledge_top_k)
    # Drop very weak matches
    hits = [h for h in hits if float(h.get("score") or 0) > 0.05] or hits[:3]

    context_blocks = []
    citations = []
    for i, h in enumerate(hits, start=1):
        context_blocks.append(
            f"[{i}] ({h.get('source_type')}/{h.get('doc_mode')}) {h.get('title')}\n{h.get('text')}"
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
            }
        )
    context = "\n\n---\n\n".join(context_blocks)
    system = (
        "You are the Doc RAG Agent for Sunny's Personal AI Agent. "
        "Answer ONLY from the provided document context. "
        "If the answer is not in context, say you don't have enough ingested evidence. "
        "Cite sources as [n]. Keep Teams-friendly length (short paragraphs + bullets)."
    )
    messages = [
        SystemMessage(content=system),
        HumanMessage(
            content=f"Question:\n{question}\n\nContext:\n{context}\n\nAnswer with citations."
        ),
    ]
    resp = await invoke_llm(messages, role="default")
    answer = (getattr(resp, "content", None) or str(resp) or "").strip()
    return {"answer": answer, "citations": citations, "hits": hits}


async def summarize_insights(
    focus: str,
    *,
    owner_session: str | None = None,
    doc_ids: list[str] | None = None,
) -> dict[str, Any]:
    docs = await kd.list_ready_documents(owner_session=owner_session, limit=40)
    if doc_ids:
        docs = [d for d in docs if d["id"] in set(doc_ids)]
    if not docs:
        return {
            "summary": "No ready ingested documents. Ingest first via *list my documents* → *ingest N*.",
            "docs_used": [],
        }

    # Prefer semantic hits when focus is a real question; else use previews of all docs
    if focus and len(focus.split()) >= 3:
        rag = await retrieve_answer(focus, owner_session=owner_session, doc_ids=doc_ids)
        context = "\n\n".join(
            f"- {c['title']} ({c['source_type']}/{c['doc_mode']}): score={c['score']}"
            for c in rag.get("citations") or []
        )
        evidence = "\n\n".join(
            f"### {h.get('title')}\n{h.get('text')}" for h in (rag.get("hits") or [])[:8]
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
        "Structure: 1) Key themes 2) Risks/gaps 3) Recommended actions 4) Open questions. "
        "Teams-friendly markdown. Do not invent facts beyond the evidence."
    )
    prompt = (
        f"Focus: {focus or 'overall corpus health'}\n\n"
        f"Corpus:\n{context}\n\nEvidence excerpts:\n{evidence}"
    )
    resp = await invoke_llm(
        [SystemMessage(content=system), HumanMessage(content=prompt)],
        role="default",
    )
    summary = (getattr(resp, "content", None) or str(resp) or "").strip()
    return {
        "summary": summary,
        "docs_used": [
            {"id": d["id"], "title": d["title"], "source_type": d["source_type"], "doc_mode": d["doc_mode"]}
            for d in docs[:20]
        ],
    }
