from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models import (
    Document,
    DocumentEmbedding,
    DocumentPage,
    ExtractedText,
    Invoice,
    InvoiceLineItem,
    ManualCorrection,
    ProcessingJob,
    User,
    ValidationResult,
)
from app.models.base import JobStage, Severity


def make_user(db, email="a@example.com"):
    u = User(email=email, password_hash="x")
    db.add(u)
    db.flush()
    return u


def make_doc(db, user, sha="a" * 64, key=None):
    d = Document(owner_id=user.id, original_filename="inv.pdf", storage_key=key or f"k-{sha[:6]}",
                 sha256=sha, mime_type="application/pdf", size_bytes=10)
    db.add(d)
    db.flush()
    return d


def test_defaults_uuid_and_timestamps(db):
    u = make_user(db)
    d = make_doc(db, u)
    assert d.id is not None and d.created_at is not None and d.updated_at is not None
    assert d.status.value == "uploaded" and d.metadata_ == {}


def test_null_money_is_distinct_from_zero(db):
    d = make_doc(db, make_user(db))
    inv = Invoice(document_id=d.id, invoice_number="INV-1", total=None, tax=Decimal("0"))
    db.add(inv)
    db.flush()
    db.expire(inv)
    assert inv.total is None
    assert inv.tax == Decimal("0.0000")


def test_decimal_precision_round_trip(db):
    d = make_doc(db, make_user(db))
    inv = Invoice(document_id=d.id, subtotal=Decimal("12345678901234.5678"))
    db.add(inv)
    db.flush()
    db.expire(inv)
    assert inv.subtotal == Decimal("12345678901234.5678")


def test_duplicate_invoices_are_allowed_not_blocked(db):
    d = make_doc(db, make_user(db))
    for _ in range(2):
        db.add(Invoice(document_id=d.id, invoice_number="INV-1", supplier_name="ACME", invoice_date=date(2025, 1, 5)))
    db.flush()  # must not raise: duplicates are flagged by validation, never constrained


def test_idempotency_key_unique(db):
    d = make_doc(db, make_user(db))
    db.add(ProcessingJob(document_id=d.id, stage=JobStage.validate, idempotency_key="k1"))
    db.flush()
    db.add(ProcessingJob(document_id=d.id, stage=JobStage.validate, idempotency_key="k1"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_page_unique_per_document(db):
    d = make_doc(db, make_user(db))
    db.add(DocumentPage(document_id=d.id, page_number=1, kind="digital"))
    db.flush()
    db.add(DocumentPage(document_id=d.id, page_number=1, kind="digital"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_invalid_enum_value_rejected_by_db(db):
    u = make_user(db)
    with pytest.raises(IntegrityError, match="ck_documents_documentstatus"):
        db.execute(text(
            "insert into documents (owner_id, original_filename, storage_key, sha256, mime_type, size_bytes,"
            " doc_type, status) values (:o,'f','kk','s','m',1,'unknown','bogus')"), {"o": u.id})


def test_optimistic_locking_rejects_stale_update(db, engine):
    d = make_doc(db, make_user(db))
    inv = Invoice(document_id=d.id, invoice_number="A")
    db.add(inv)
    db.flush()
    db.execute(text("update invoices set version = version + 1 where id = :i"), {"i": inv.id})
    inv.invoice_number = "B"
    with pytest.raises(StaleDataError):
        db.flush()


def test_cascade_delete_removes_children(db):
    d = make_doc(db, make_user(db))
    inv = Invoice(document_id=d.id, line_items=[InvoiceLineItem(line_no=1, description="x")])
    db.add(inv)
    db.flush()
    db.execute(text("delete from documents where id=:i"), {"i": d.id})
    assert db.execute(text("select count(*) from invoice_line_items")).scalar() == 0


def test_line_no_unique_per_invoice(db):
    d = make_doc(db, make_user(db))
    inv = Invoice(document_id=d.id)
    db.add(inv)
    db.flush()
    db.add_all([InvoiceLineItem(invoice_id=inv.id, line_no=1), InvoiceLineItem(invoice_id=inv.id, line_no=1)])
    with pytest.raises(IntegrityError):
        db.flush()


def test_full_text_search_generated_column(db):
    d = make_doc(db, make_user(db))
    db.add(ExtractedText(document_id=d.id, page_number=1, text="Invoice from Rahim Traders Dhaka", source="native"))
    db.flush()
    rows = db.execute(text(
        "select page_number from extracted_text where tsv @@ websearch_to_tsquery('simple','rahim')")).all()
    assert rows == [(1,)]


def test_pgvector_cosine_ranking(db):
    d = make_doc(db, make_user(db))
    near = [1.0] + [0.0] * 383
    far = [0.0, 1.0] + [0.0] * 382
    db.add_all([
        DocumentEmbedding(document_id=d.id, chunk_index=0, chunk_text="near", embedding=near, model_name="m"),
        DocumentEmbedding(document_id=d.id, chunk_index=1, chunk_text="far", embedding=far, model_name="m"),
    ])
    db.flush()
    top = db.execute(select(DocumentEmbedding.chunk_text).order_by(
        DocumentEmbedding.embedding.cosine_distance(near)).limit(1)).scalar()
    assert top == "near"


def test_validation_result_defaults_and_audit_rows(db):
    u = make_user(db)
    d = make_doc(db, u)
    inv = Invoice(document_id=d.id)
    db.add(inv)
    db.flush()
    vr = ValidationResult(invoice_id=inv.id, rule_id="total_mismatch", severity=Severity.error,
                          expected_value="1150.00", actual_value="1200.00", description="Total != subtotal + tax")
    mc = ManualCorrection(invoice_id=inv.id, field_name="total", old_value="1200.00", new_value="1150.00", user_id=u.id)
    db.add_all([vr, mc])
    db.flush()
    assert vr.resolution_status.value == "open"
    assert mc.created_at is not None
