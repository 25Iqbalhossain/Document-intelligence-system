"""Embedding providers.

`hash` is a deterministic hashed bag-of-words embedding (feature hashing of word unigrams + bigrams, L2-normalised).
It needs no model download, so the pipeline and tests run offline, but it only captures *lexical* overlap:
it does NOT understand synonyms or meaning. Use `sentence_transformers` for real semantic search.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from app.core.config import Settings, get_settings

_TOKEN = re.compile(r"\w+", re.UNICODE)


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedding:
    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.name = f"hash-bow-{dim}"

    def _vec(self, text: str) -> list[float]:
        toks = [t.lower() for t in _TOKEN.findall(text)]
        feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
        v = [0.0] * self.dim
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0  # signed hashing reduces collision bias
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v] if norm else v

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class SentenceTransformerEmbedding:
    def __init__(self, model: str, dim: int) -> None:
        from sentence_transformers import SentenceTransformer  # optional heavy dependency

        self._m = SentenceTransformer(model)
        self.name, self.dim = model, dim
        actual = self._m.get_sentence_embedding_dimension()
        if actual != dim:
            raise ValueError(f"Model {model} produces {actual} dims but the database column is {dim}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._m.encode(texts, normalize_embeddings=True)]


def get_embedder(settings: Settings | None = None) -> EmbeddingProvider:
    s = settings or get_settings()
    if s.embedding_provider == "sentence_transformers":
        return SentenceTransformerEmbedding(s.embedding_model, s.embedding_dim)
    return HashEmbedding(s.embedding_dim)
