import csv
import io
import uuid
from decimal import Decimal as D

import pytest

from app.models import DocumentRelationship, Invoice
from app.models.base import MatchStatus
from app.models.invoices import RelationType
from app.services.reconciliation_service import (
    ReconciliationError,
    build_report,
    confirm_match,
    reject_match,
    report_to_csv,
    run_reconciliation,
)
from tests.integration.test_schema import make_doc, make_user
from tests.integration.test_validation_service import make_invoice


def two_docs(db):
    u = make_user(db, f"{uuid.uuid4().hex[:8]}@example.com")
    mk = lambda: make_doc(db, u, sha=uuid.uuid4().hex + uuid.uuid4().hex, key=uuid.uuid4().hex)  # noqa: E731
    return mk(), mk()


def test_full_run_report_and_csv(db):
    a, b = two_docs(db)
    make_invoice(db, a, invoice_number="INV-1", total=D("1150"))
    make_invoice(db, b, invoice_number="INV-1", total=D("1200"))  # discrepancy
    make_invoice(db, a, invoice_number="ONLY-A", total=D("5"))
    run = run_reconciliation(db, [a.id], [b.id])
    rep = build_report(db, run.id)
    assert rep["summary"]["matched"] == 1 and len(rep["summary"]["unmatched_left"]) == 1
    (m,) = rep["matches"]
    assert m["method"] == "exact_supplier" and m["status"] == "confirmed" and m["outcome"] == "discrepancies"
    assert any(d["field"] == "total" and d["severity"] == "error" for d in m["discrepancies"])
    rows = list(csv.reader(io.StringIO(report_to_csv(rep))))
    assert rows[0][0] == "row_type" and any(r[0] == "unmatched_left" for r in rows)


def test_invoices_untouched_and_duplicates_flagged_not_deleted(db):
    a, b = two_docs(db)
    i1 = make_invoice(db, a, invoice_number="D-1")
    i2 = make_invoice(db, a, invoice_number="D-1")
    make_invoice(db, b, invoice_number="D-1")
    run = run_reconciliation(db, [a.id], [b.id])
    dups = db.query(DocumentRelationship).filter_by(run_id=run.id, relation_type=RelationType.duplicate_of).all()
    assert len(dups) == 1 and dups[0].status == MatchStatus.proposed
    for i in (i1, i2):
        db.refresh(i)
        assert i.deleted_at is None
    assert db.query(Invoice).filter(Invoice.document_id == a.id).count() == 2


def test_confirm_and_reject_audit_user(db):
    a, b = two_docs(db)
    make_invoice(db, a, invoice_number="INV-9", supplier_name="ACME")
    make_invoice(db, b, invoice_number="INV-9", supplier_name="Zed Corp")  # number-only -> proposed
    run = run_reconciliation(db, [a.id], [b.id])
    (m,) = build_report(db, run.id)["matches"]
    assert m["status"] == "proposed" and m["outcome"] == "needs_confirmation" or m["outcome"] == "discrepancies"
    user = make_user(db, f"{uuid.uuid4().hex[:8]}@example.com")
    rel = confirm_match(db, uuid.UUID(m["id"]), user.id)
    assert rel.status == MatchStatus.confirmed and rel.confirmed_by == user.id
    assert reject_match(db, rel.id, user.id).status == MatchStatus.rejected


def test_csv_neutralises_formula_injection(db):
    a, b = two_docs(db)
    a.original_filename = "=HYPERLINK(1).pdf"
    make_invoice(db, a, invoice_number="Q-1")
    db.flush()
    run = run_reconciliation(db, [a.id], [b.id])
    out = report_to_csv(build_report(db, run.id))
    assert ",=HYPERLINK" not in out and "'=HYPERLINK" in out


def test_input_validation(db):
    a, b = two_docs(db)
    with pytest.raises(ReconciliationError):
        run_reconciliation(db, [a.id], [a.id])
    with pytest.raises(ReconciliationError):
        run_reconciliation(db, [], [b.id])
    with pytest.raises(ReconciliationError):
        run_reconciliation(db, [uuid.uuid4()], [b.id])
