import uuid

from sqlalchemy import func, select

from app.models import DocumentEmbedding, ExtractedText, Invoice
from app.models.base import UserRole
from app.search.embeddings import HashEmbedding
from app.services.embedding_service import run_embedding
from app.services.search_service import search
from tests.integration.test_api import mkuser
from tests.integration.test_schema import make_doc

E = HashEmbedding()


def add_doc(db, owner, pages: dict[int, str]):
    doc = make_doc(db, owner, sha=uuid.uuid4().hex * 2, key=uuid.uuid4().hex)
    for n, t in pages.items():
        db.add(ExtractedText(document_id=doc.id, page_number=n, text=t, source="native"))
    db.flush()
    run_embedding(db, doc.id, E)
    return doc


def go(db, user, q, mode="hybrid"):
    return search(db, q, user_id=user.id, privileged=user.role in (UserRole.admin, UserRole.reviewer),
                  embedder=E, mode=mode)


def test_embedding_is_idempotent_and_page_granular(db):
    u = mkuser(db, UserRole.uploader)
    doc = add_doc(db, u, {1: "alpha beta gamma", 2: "delta " * 400})
    n1 = db.scalar(select(func.count()).select_from(DocumentEmbedding).where(DocumentEmbedding.document_id == doc.id))
    assert n1 >= 3  # page 2 is split into several chunks
    run_embedding(db, doc.id, E)
    n2 = db.scalar(select(func.count()).select_from(DocumentEmbedding).where(DocumentEmbedding.document_id == doc.id))
    assert n1 == n2


def test_fts_finds_exact_terms_with_highlight_and_page(db):
    u = mkuser(db, UserRole.uploader)
    needle = f"zq{uuid.uuid4().hex[:8]}"
    d = add_doc(db, u, {1: "nothing relevant here", 2: f"Payment reference {needle} for cement"})
    out = go(db, u, needle, "fts")
    (r,) = out["results"]
    assert r["document_id"] == str(d.id) and r["page"] == 2 and f"<b>{needle}</b>" in r["snippet"]
    assert r["ranks"] == {"fts": 1}


def test_vector_finds_similar_text_and_ignores_unrelated(db):
    u = mkuser(db, UserRole.uploader)
    tag = uuid.uuid4().hex[:6]
    d1 = add_doc(db, u, {1: f"cement bag delivery {tag} dhaka warehouse"})
    add_doc(db, u, {1: "completely different subject matter about taxes"})
    out = go(db, u, f"cement delivery {tag}", "vector")
    assert out["results"][0]["document_id"] == str(d1.id)
    assert out["semantic"] is False and out["embedding_model"] == "hash-bow-384"


def test_hybrid_reports_both_ranks(db):
    u = mkuser(db, UserRole.uploader)
    tag = f"hy{uuid.uuid4().hex[:8]}"
    add_doc(db, u, {1: f"invoice {tag} total due"})
    top = go(db, u, tag)["results"][0]
    assert set(top["ranks"]) == {"fts", "vector"}


def test_search_is_scoped_to_owner_but_not_for_reviewers(db):
    a, b, rev = mkuser(db, UserRole.uploader), mkuser(db, UserRole.uploader), mkuser(db, UserRole.reviewer)
    tag = f"sc{uuid.uuid4().hex[:8]}"
    add_doc(db, a, {1: f"secret {tag}"})
    for mode in ("fts", "vector", "hybrid"):
        assert go(db, b, tag, mode)["results"] == []
        assert len(go(db, a, tag, mode)["results"]) == 1
    assert len(go(db, rev, tag)["results"]) == 1


def test_deleted_documents_not_searchable(db):
    u = mkuser(db, UserRole.uploader)
    tag = f"dl{uuid.uuid4().hex[:8]}"
    d = add_doc(db, u, {1: f"gone {tag}"})
    d.deleted_at = func.now()
    db.flush()
    assert go(db, u, tag)["results"] == []


def test_invoice_search_by_number_variant_and_supplier(db):
    u = mkuser(db, UserRole.uploader)
    d = add_doc(db, u, {1: "x"})
    sup = f"Zeta{uuid.uuid4().hex[:6]} Traders"
    db.add(Invoice(document_id=d.id, invoice_number="INV-2025/0042", supplier_name=sup))
    db.flush()
    assert go(db, u, sup.split()[0])["invoices"][0]["supplier_name"] == sup
    assert any(i["invoice_number"] == "INV-2025/0042" for i in go(db, u, "inv 2025 0042")["invoices"])


def test_hostile_queries_do_not_break(db):
    u = mkuser(db, UserRole.uploader)
    for q in ["'; DROP TABLE documents; --", "%%%", "a & | ! ( ", "\\", '"unterminated', "ঢাকা বিল"]:
        out = go(db, u, q)
        assert isinstance(out["results"], list)
    assert db.scalar(select(func.count()).select_from(DocumentEmbedding)) >= 0  # tables intact
