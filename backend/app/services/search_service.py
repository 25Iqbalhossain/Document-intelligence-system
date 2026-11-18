"""Hybrid search: PostgreSQL full-text + pgvector similarity, merged by reciprocal rank fusion (RRF).

Access control is applied inside every query (owner, or reviewer/admin), never after the fact.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.extraction.normalizers import invoice_key
from app.models import Document, DocumentEmbedding, ExtractedText, Invoice
from app.search.embeddings import EmbeddingProvider

Mode = Literal["hybrid", "fts", "vector"]
RRF_K = 60
CANDIDATES = 60
MAX_QUERY = 500


@dataclass(slots=True)
class Hit:
    document_id: uuid.UUID
    filename: str
    page: int | None
    snippet: str
    score: float = 0.0
    ranks: dict[str, int] = field(default_factory=dict)
    raw: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"document_id": str(self.document_id), "filename": self.filename, "page": self.page,
                "snippet": self.snippet, "score": round(self.score, 6), "ranks": self.ranks,
                "raw_scores": {k: round(v, 4) for k, v in self.raw.items()}}


def _scope(stmt, user_id: uuid.UUID, privileged: bool):
    stmt = stmt.where(Document.deleted_at.is_(None))
    return stmt if privileged else stmt.where(Document.owner_id == user_id)


def fts_hits(session: Session, q: str, user_id: uuid.UUID, privileged: bool, limit: int = CANDIDATES) -> list[Hit]:
    tsq = func.websearch_to_tsquery("simple", q)
    rank = func.ts_rank_cd(ExtractedText.tsv, tsq).label("rank")
    snippet = func.ts_headline("simple", ExtractedText.text, tsq,
                               "MaxFragments=2, MaxWords=28, MinWords=8, FragmentDelimiter= … ").label("snip")
    stmt = (select(ExtractedText.document_id, ExtractedText.page_number, Document.original_filename, snippet, rank)
            .join(Document, Document.id == ExtractedText.document_id).where(ExtractedText.tsv.op("@@")(tsq)))
    rows = session.execute(_scope(stmt, user_id, privileged).order_by(rank.desc(), ExtractedText.document_id,
                                                                      ExtractedText.page_number).limit(limit))
    return [Hit(r.document_id, r.original_filename, r.page_number, r.snip, raw={"fts": float(r.rank)}) for r in rows]


def vector_hits(session: Session, embedder: EmbeddingProvider, q: str, user_id: uuid.UUID, privileged: bool,
                limit: int = CANDIDATES) -> list[Hit]:
    vec = embedder.embed([q])[0]
    dist = DocumentEmbedding.embedding.cosine_distance(vec).label("dist")
    # Filtered ANN search can return fewer rows than `limit`; widen the HNSW candidate list for this query.
    session.execute(text("SET LOCAL hnsw.ef_search = 200"))
    stmt = (select(DocumentEmbedding.document_id, DocumentEmbedding.page_number, DocumentEmbedding.chunk_text,
                   Document.original_filename, dist)
            .join(Document, Document.id == DocumentEmbedding.document_id)
            .where(DocumentEmbedding.model_name == embedder.name))
    rows = session.execute(_scope(stmt, user_id, privileged).order_by(dist).limit(limit))
    out: list[Hit] = []
    for r in rows:
        sim = 1.0 - float(r.dist)
        if sim <= 0:  # no overlap at all: not a hit
            continue
        out.append(Hit(r.document_id, r.original_filename, r.page_number, r.chunk_text[:240], raw={"vector": sim}))
    return out


def fuse(lists: dict[str, list[Hit]], limit: int) -> list[Hit]:
    """Reciprocal rank fusion keyed on (document, page). Each source keeps its own rank for explainability."""
    merged: dict[tuple[uuid.UUID, int | None], Hit] = {}
    for source, hits in lists.items():
        seen: set[tuple[uuid.UUID, int | None]] = set()
        rank = 0
        for h in hits:
            key = (h.document_id, h.page)
            if key in seen:  # several chunks of one page: only its best chunk counts
                continue
            seen.add(key)
            rank += 1
            m = merged.setdefault(key, Hit(h.document_id, h.filename, h.page, h.snippet))
            m.score += 1.0 / (RRF_K + rank)
            m.ranks[source] = rank
            m.raw.update(h.raw)
            if source == "fts":
                m.snippet = h.snippet  # highlighted full-text snippet is the most useful one to show
    return sorted(merged.values(), key=lambda h: (-h.score, str(h.document_id), h.page or 0))[:limit]


def invoice_hits(
    session: Session, q: str, user_id: uuid.UUID, privileged: bool, limit: int = 20
) -> list[dict[str, Any]]:
    like = "%" + re.sub(r"[%_\\]", lambda m: "\\" + m.group(0), q.strip()) + "%"
    conds = [Invoice.invoice_number.ilike(like), Invoice.supplier_name.ilike(like), Invoice.customer_name.ilike(like)]
    key = invoice_key(q)
    if key:
        conds.append(func.regexp_replace(func.upper(Invoice.invoice_number), "[^A-Z0-9]", "", "g") == key)
    stmt = (select(Invoice, Document.original_filename).join(Document, Document.id == Invoice.document_id)
            .where(Invoice.deleted_at.is_(None), or_(*conds)))
    rows = session.execute(_scope(stmt, user_id, privileged).order_by(Invoice.created_at.desc()).limit(limit))
    return [{"invoice_id": str(i.id), "document_id": str(i.document_id), "filename": fn,
             "invoice_number": i.invoice_number, "supplier_name": i.supplier_name, "total": str(i.total)
             if i.total is not None else None, "currency": i.currency, "review_status": i.review_status.value}
            for i, fn in rows]


def search(session: Session, q: str, *, user_id: uuid.UUID, privileged: bool, embedder: EmbeddingProvider,
           mode: Mode = "hybrid", limit: int = 20) -> dict[str, Any]:
    q = q.strip()
    if not q:
        raise ValueError("Empty query")
    if len(q) > MAX_QUERY:
        raise ValueError(f"Query longer than {MAX_QUERY} characters")
    lists: dict[str, list[Hit]] = {}
    if mode in ("hybrid", "fts"):
        lists["fts"] = fts_hits(session, q, user_id, privileged)
    if mode in ("hybrid", "vector"):
        lists["vector"] = vector_hits(session, embedder, q, user_id, privileged)
    hits = fuse(lists, limit)
    return {"query": q, "mode": mode, "embedding_model": embedder.name,
            "semantic": not embedder.name.startswith("hash-"),  # hash embeddings are lexical only
            "results": [h.to_dict() for h in hits], "invoices": invoice_hits(session, q, user_id, privileged)}
