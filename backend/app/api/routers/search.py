from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import PRIVILEGED, current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models import User
from app.search.embeddings import EmbeddingProvider, get_embedder
from app.services.search_service import MAX_QUERY, search

router = APIRouter(tags=["search"])


@lru_cache
def _embedder() -> EmbeddingProvider:
    return get_embedder(get_settings())


def embedder_dep() -> EmbeddingProvider:
    return _embedder()


@router.get("/search")
def search_endpoint(
    q: str = Query(min_length=1, max_length=MAX_QUERY), mode: Literal["hybrid", "fts", "vector"] = "hybrid",
    limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(current_user),
    embedder: EmbeddingProvider = Depends(embedder_dep),
):
    try:
        return search(db, q, user_id=user.id, privileged=user.role in PRIVILEGED, embedder=embedder, mode=mode,
                      limit=limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
