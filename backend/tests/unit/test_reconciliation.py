from datetime import date
from decimal import Decimal as D

from app.models.base import MatchStatus
from app.reconciliation.comparator import compare_records
from app.reconciliation.matcher import Rec, match_records
from app.validation.types import InvoiceData


def inv(i, num, sup, total, d=date(2024, 1, 5), **kw):
    return InvoiceData(id=str(i), invoice_number=num, supplier_name=sup, total=D(total) if total else None,
                       invoice_date=d, **kw)


def rec(i, num, sup, total, d=date(2024, 1, 5), side="L"):
    return Rec(inv(i, num, sup, total, d), document_id=side, label=f"{side}{i}")


def kinds(ds):
    return {(x.field, x.kind, x.severity) for x in ds}


def test_comparator_identical_has_no_discrepancies():
    a = inv(1, "INV-1", "ACME Ltd", "100.00", currency="USD")
    assert compare_records(a, inv(2, "INV-1", "ACME Ltd", "100.00", currency="usd")) == []


def test_comparator_format_difference_is_info_and_total_error():
    ds = compare_records(inv(1, "INV-1", "A", "1150.00"), inv(2, "inv 1", "A", "1200.00"))
    assert ("invoice_number", "format_difference", "info") in kinds(ds)
    assert ("total", "value_mismatch", "error") in kinds(ds)


def test_comparator_tolerance_and_missing():
    ds = compare_records(inv(1, "X1", "A", "100.00"), inv(2, "X1", "A", "100.01"))
    assert ds == []
    ds = compare_records(inv(1, "X1", "A", "100.00", currency="USD"), inv(2, "X1", "A", "100.00"))
    assert any(x.kind == "missing_in_right" for x in ds)


def test_comparator_different_number_is_error_and_date_mismatch():
    ds = compare_records(inv(1, "A1", "A", "5"), inv(2, "B2", "A", "5", d=date(2024, 2, 1)))
    assert ("invoice_number", "value_mismatch", "error") in {(x.field, x.kind, x.severity) for x in ds}
    assert any(x.field == "invoice_date" and x.severity == "error" for x in ds)


def test_exact_match_is_confirmed():
    out = match_records([rec(1, "INV-001", "ACME Ltd", "100")], [rec(2, "inv 001", "Acme Ltd.", "100", side="R")])
    assert len(out.pairs) == 1
    p = out.pairs[0]
    assert (p.method, p.status) == ("exact_supplier", MatchStatus.confirmed)
    assert not out.unmatched_left and not out.unmatched_right


def test_number_only_match_is_proposed_not_confirmed():
    out = match_records([rec(1, "INV-9", "ACME Ltd", "100")], [rec(2, "INV-9", "Totally Other Co", "100", side="R")])
    assert out.pairs[0].method == "exact_number"
    assert out.pairs[0].status == MatchStatus.proposed


def test_fuzzy_needs_corroboration_and_is_proposed():
    out = match_records([rec(1, "INV-00123", "ACME Ltd", "100")], [rec(2, "INV-0123", "ACME Ltd", "100", side="R")])
    assert [(p.method, p.status) for p in out.pairs] == [("fuzzy", MatchStatus.proposed)]


def test_fuzzy_never_merges_adjacent_numbers_with_different_amounts():
    out = match_records([rec(1, "INV-0042", "ACME Ltd", "100")],
                        [rec(2, "INV-0043", "ACME Ltd", "250", d=date(2024, 3, 1), side="R")])
    assert out.pairs == []
    assert len(out.unmatched_left) == 1 and len(out.unmatched_right) == 1


def test_duplicates_are_reported_not_deleted_and_not_auto_matched():
    left = [rec(1, "INV-5", "ACME", "100"), rec(2, "INV-5", "ACME", "999", d=date(2024, 2, 2))]
    right = [rec(3, "INV-5", "ACME", "100", side="R")]
    out = match_records(left, right)
    assert len(out.duplicates_left) == 1 and len(out.duplicates_left[0]) == 2
    # the identical-date/total pair is proposed (never confirmed); the other record stays visible
    assert out.pairs[0].status == MatchStatus.proposed
    assert {r.id for r in out.unmatched_left} | {x["left_ids"][0] for x in out.ambiguous if x["left_ids"]} >= {"2"}


def test_attribute_match_when_number_missing():
    out = match_records([rec(1, None, "ACME Ltd", "100.00")], [rec(2, "INV-7", "ACME Ltd", "100.00", side="R")])
    assert [(p.method, p.status) for p in out.pairs] == [("attributes", MatchStatus.proposed)]


def test_attribute_tie_is_ambiguous():
    out = match_records([rec(1, None, "ACME Ltd", "100")],
                        [rec(2, "A-1", "ACME Ltd", "100", side="R"), rec(3, "A-2", "ACME Ltd", "100", side="R")])
    assert out.pairs == [] and out.ambiguous


def test_unmatched_both_sides_and_missing_id_rejected():
    out = match_records([rec(1, "A-1", "X", "1")], [rec(2, "B-9", "Y", "2", side="R")])
    assert len(out.unmatched_left) == 1 and len(out.unmatched_right) == 1
    import pytest
    with pytest.raises(ValueError):
        match_records([Rec(InvoiceData(), "d", "l")], [])
