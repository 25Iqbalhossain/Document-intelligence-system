import uuid
from datetime import date
from decimal import Decimal as D

from sqlalchemy import select

from app.models import Invoice, InvoiceLineItem, ValidationResult
from app.models.base import ResolutionStatus, ReviewStatus, Severity
from app.services.validation_service import find_existing, run_validation
from tests.integration.test_schema import make_doc, make_user

TODAY = date(2025, 6, 1)


def make_invoice(db, doc, **kw):
    base = dict(document_id=doc.id, invoice_number="INV-1", invoice_date=date(2025, 1, 5), supplier_name="ACME Ltd",
                subtotal=D("1000"), tax=D("150"), total=D("1150"), currency="BDT")
    base.update(kw)
    inv = Invoice(**base)
    db.add(inv)
    db.flush()
    return inv


def rows(db, inv):
    return db.scalars(select(ValidationResult).where(ValidationResult.invoice_id == inv.id)).all()


def setup(db):
    user = make_user(db, f"{uuid.uuid4().hex[:8]}@example.com")
    return user, make_doc(db, user, sha=uuid.uuid4().hex + uuid.uuid4().hex, key=uuid.uuid4().hex)


def test_findings_persisted_with_all_required_fields(db):
    _, doc = setup(db)
    inv = make_invoice(db, doc, total=D("1200"),
                       source_references=[{"field": "total", "page": 2, "text": "Total 1,200.00"}])
    run_validation(db, inv.id, today=TODAY)
    (r,) = [r for r in rows(db, inv) if r.rule_id == "total_mismatch"]
    assert (r.severity, r.expected_value, r.actual_value) == (Severity.error, "1150.00", "1200.00")
    assert r.field_name == "total" and r.description and r.resolution_status == ResolutionStatus.open
    assert r.source_references == [{"field": "total", "page": 2, "text": "Total 1,200.00"}]
    db.refresh(inv)
    assert inv.requires_review and inv.review_status == ReviewStatus.pending


def test_clean_invoice_does_not_force_review(db):
    _, doc = setup(db)
    inv = make_invoice(db, doc, line_items=[InvoiceLineItem(line_no=1, quantity=D("10"), unit_price=D("100"),
                                                            total=D("1000"))])
    run_validation(db, inv.id, today=TODAY)
    db.refresh(inv)
    assert not inv.requires_review and inv.review_status == ReviewStatus.not_required
    assert [r for r in rows(db, inv) if r.severity != Severity.info] == []


def test_rerun_is_idempotent(db):
    _, doc = setup(db)
    inv = make_invoice(db, doc, total=D("1200"), supplier_name=None)
    run_validation(db, inv.id, today=TODAY)
    first = {(r.id, r.rule_id) for r in rows(db, inv)}
    run_validation(db, inv.id, today=TODAY)
    assert {(r.id, r.rule_id) for r in rows(db, inv)} == first


def test_reviewer_decision_survives_rerun(db):
    user, doc = setup(db)
    inv = make_invoice(db, doc, total=D("1200"))
    run_validation(db, inv.id, today=TODAY)
    (r,) = [r for r in rows(db, inv) if r.rule_id == "total_mismatch"]
    r.resolution_status, r.resolved_by, r.resolution_note = ResolutionStatus.accepted, user.id, "supplier rounding"
    db.flush()
    run_validation(db, inv.id, today=TODAY)
    (again,) = [r for r in rows(db, inv) if r.rule_id == "total_mismatch"]
    assert again.id == r.id and again.resolution_status == ResolutionStatus.accepted
    assert again.resolution_note == "supplier rounding"


def test_fixed_value_removes_stale_open_finding_but_keeps_resolved_history(db):
    user, doc = setup(db)
    inv = make_invoice(db, doc, total=D("1200"), invoice_number=None)
    run_validation(db, inv.id, today=TODAY)
    for r in rows(db, inv):
        if r.rule_id == "required_field_missing":
            r.resolution_status = ResolutionStatus.corrected
    db.flush()
    inv.total, inv.invoice_number = D("1150"), "INV-1"  # reviewer corrects both fields
    db.flush()
    run_validation(db, inv.id, today=TODAY)
    left = {r.rule_id for r in rows(db, inv)}
    assert "total_mismatch" not in left               # open + no longer applies -> removed
    assert "required_field_missing" in left            # resolved -> kept as history


def test_duplicate_detected_across_documents_with_normalised_numbers(db):
    user, doc1 = setup(db)
    _, doc2 = setup(db)
    old = make_invoice(db, doc1, invoice_number="inv 1", supplier_name="ACME LIMITED")
    new = make_invoice(db, doc2, invoice_number="INV-1")
    assert [e.id for e in find_existing(db, new)] == [str(old.id)]
    run_validation(db, new.id, today=TODAY)
    f = [r for r in rows(db, new) if r.rule_id == "duplicate_invoice"]
    assert len(f) == 1 and str(old.id) in f[0].actual_value
    assert db.get(Invoice, old.id) is not None  # detection never deletes


def test_soft_deleted_invoice_is_not_a_duplicate(db):
    from datetime import UTC, datetime
    _, doc1 = setup(db)
    _, doc2 = setup(db)
    make_invoice(db, doc1, deleted_at=datetime.now(UTC))
    new = make_invoice(db, doc2)
    assert find_existing(db, new) == []


def test_duplicate_lookup_uses_the_expression_index(db):
    from sqlalchemy import text
    db.execute(text("set local enable_seqscan = off"))
    plan = "\n".join(r[0] for r in db.execute(text(
        "explain select id from invoices where regexp_replace(upper(invoice_number), '[^A-Z0-9]', '', 'g') = 'INV1'")))
    assert "ix_invoices_number_key" in plan


def test_approval_withdrawn_only_for_new_blocking_error(db):
    _, doc = setup(db)
    inv = make_invoice(db, doc)
    run_validation(db, inv.id, today=TODAY)
    inv.review_status = ReviewStatus.approved
    db.flush()
    run_validation(db, inv.id, today=TODAY)
    assert inv.review_status == ReviewStatus.approved   # nothing new -> approval stands
    inv.total = D("5000")
    db.flush()
    run_validation(db, inv.id, today=TODAY)
    assert inv.review_status == ReviewStatus.pending    # new error -> approval withdrawn


def test_validation_never_grants_approval(db):
    _, doc = setup(db)
    inv = make_invoice(db, doc)
    run_validation(db, inv.id, today=TODAY)
    assert inv.review_status != ReviewStatus.approved
