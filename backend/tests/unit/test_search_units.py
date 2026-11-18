import math
import uuid

import pytest

from app.search.chunking import chunk_text
from app.search.embeddings import HashEmbedding
from app.services.search_service import RRF_K, Hit, fuse


def cos(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_hash_embedding_deterministic_normalised_dimension():
    e = HashEmbedding(384)
    v1, v2 = e.embed(["Cement bag invoice"])[0], e.embed(["Cement bag invoice"])[0]
    assert v1 == v2 and len(v1) == 384 and math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0)
    assert e.embed([""])[0] == [0.0] * 384  # empty text: zero vector, no division by zero


def test_hash_embedding_is_lexical_not_semantic():
    e = HashEmbedding()
    a, same, related, other = e.embed(["cement bag delivery", "cement bag delivery note", "concrete sack shipment",
                                        "quarterly tax filing"])
    assert cos(a, same) > 0.5
    assert cos(a, related) < 0.3  # synonyms are NOT understood: documented limitation of the offline embedder


def test_chunking_bounds_overlap_and_coverage():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_text(text, max_chars=200, overlap=40)
    assert all(len(c) <= 200 for c in chunks) and len(chunks) > 5
    assert chunks[0].startswith("word0") and chunks[-1].endswith("word999")
    seen = set(" ".join(chunks).split())
    assert seen == set(text.split())  # nothing lost
    assert chunk_text("   ") == [] and chunk_text("short") == ["short"]
    with pytest.raises(ValueError):
        chunk_text("x", max_chars=10, overlap=10)


def test_chunking_always_terminates_on_unbroken_text():
    assert len(chunk_text("a" * 5000, max_chars=1000, overlap=150)) >= 5


def h(doc, page, source_raw):
    return Hit(doc, "f.pdf", page, "snip", raw=source_raw)


def test_rrf_rewards_agreement_and_keeps_ranks():
    d1, d2, d3 = (uuid.uuid4() for _ in range(3))
    fts = [h(d1, 1, {"fts": 0.9}), h(d2, 1, {"fts": 0.5})]
    vec = [h(d3, 1, {"vector": 0.9}), h(d2, 1, {"vector": 0.8})]
    out = fuse({"fts": fts, "vector": vec}, 10)
    assert out[0].document_id == d2  # found by both beats a first place in one list
    assert out[0].ranks == {"fts": 2, "vector": 2}
    assert math.isclose(out[0].score, 2 / (RRF_K + 2))


def test_rrf_counts_a_page_once_per_source():
    d = uuid.uuid4()
    out = fuse({"vector": [h(d, 1, {"vector": 0.9}), h(d, 1, {"vector": 0.8}), h(d, 2, {"vector": 0.7})]}, 10)
    assert [(x.page, x.ranks["vector"]) for x in out] == [(1, 1), (2, 2)]


def test_rrf_deterministic_ties_and_limit():
    a, b = sorted([uuid.uuid4(), uuid.uuid4()], key=str)
    out = fuse({"fts": [h(b, 1, {}), h(a, 1, {})], "vector": [h(a, 1, {}), h(b, 1, {})]}, 1)
    assert len(out) == 1 and out[0].document_id == a
