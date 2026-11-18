"""Embedding stage: chunk each page's text and store vectors. Idempotent (replaces this model's chunks)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import DocumentEmbedding, ExtractedText
from app.search.chunking import chunk_text
from app.search.embeddings import EmbeddingProvider

BATCH = 64


@dataclass(slots=True)
class EmbedSummary:
    chunks: int
    pages: int
    model: str


def run_embedding(session: Session, document_id: uuid.UUID, embedder: EmbeddingProvider) -> EmbedSummary:
    session.execute(delete(DocumentEmbedding).where(DocumentEmbedding.document_id == document_id,
                                                    DocumentEmbedding.model_name == embedder.name))
    pending: list[tuple[int, int, str]] = []
    total = pages = 0

    def flush() -> None:
        nonlocal total
        if not pending:
            return
        vecs = embedder.embed([c for _, _, c in pending])
        for (page, idx, chunk), vec in zip(pending, vecs, strict=True):
            session.add(DocumentEmbedding(document_id=document_id, page_number=page, chunk_index=idx,
                                          chunk_text=chunk, embedding=vec, model_name=embedder.name))
        session.flush()
        total += len(pending)
        pending.clear()

    # one page at a time, so a 1,000-page document never holds all its text in memory
    page_numbers = session.scalars(select(ExtractedText.page_number).where(ExtractedText.document_id == document_id)
                                   .distinct().order_by(ExtractedText.page_number)).all()
    for page in page_numbers:
        texts = session.scalars(select(ExtractedText.text).where(ExtractedText.document_id == document_id,
                                                                 ExtractedText.page_number == page)).all()
        chunks = chunk_text("\n".join(texts))
        pages += bool(chunks)
        pending.extend((page, i, c) for i, c in enumerate(chunks))
        if len(pending) >= BATCH:
            flush()
    flush()
    return EmbedSummary(chunks=total, pages=pages, model=embedder.name)
