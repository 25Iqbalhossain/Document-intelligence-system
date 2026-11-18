from datetime import date
from decimal import Decimal as D

from app.models.base import Severity
from app.validation.config import ValidationConfig
from app.validation.engine import has_blocking_errors, validate_invoice
from app.validation.types import ExistingInvoice, InvoiceData, LineData

TODAY = date(2025, 6, 1)


def inv(**kw):
    base = dict(id="new", invoice_number="INV-1", invoice_date=date(2025, 1, 5), supplier_name="ACME Ltd",
                customer_name="Buyer", subtotal=D("1000"), tax=D("150"), total=D("1150"), currency="BDT")
    base.update(kw)
    return InvoiceData(**base)


def line(n, q, p, t, tax=None):
    return LineData(line_no=n, quantity=D(q) if q is not None else None, unit_price=D(p) if p is not None else None,
                    total=D(t) if t is not None else None, tax=D(tax) if tax is not None else None)


def run(i, cfg=None, existing=(), today=TODAY):
    return validate_invoice(i, cfg, existing, today)


def ids(findings, sev=None):
    return [f.rule_id for f in findings if sev is None or f.severity == sev]


def only(findings, rule_id):
    (f,) = [f for f in findings if f.rule_id == rule_id]
    return f


def test_clean_invoice_has_no_errors_or_warnings():
    fs = run(inv(line_items=[line(1, 10, 100, 1000)]))
    assert ids(fs, Severity.error) == [] and ids(fs, Severity.warning) == []


# 1 required fields
def test_missing_required_fields_reported_individually():
    fs = run(inv(invoice_number=None, supplier_name="  ", invoice_date=None, total=None))
    assert sorted(f.field for f in fs if f.rule_id == "required_field_missing") == [
        "invoice_date", "invoice_number", "supplier_name", "total"]


def test_zero_total_is_present_not_missing():
    fs = run(inv(subtotal=D("0"), tax=D("0"), total=D("0")))
    assert "required_field_missing" not in ids(fs)


def test_required_fields_configurable():
    fs = run(inv(customer_name=None), ValidationConfig(required_fields=("customer_name",)))
    assert ids(fs, Severity.error) == ["required_field_missing"]


# 2 dates
def test_future_and_ancient_dates_flagged():
    assert "invalid_invoice_date" in ids(run(inv(invoice_date=date(2026, 1, 1))))
    assert "invalid_invoice_date" in ids(run(inv(invoice_date=date(1985, 1, 1))))
    assert "invalid_invoice_date" not in ids(run(inv(invoice_date=TODAY)))


def test_ambiguous_date_warning():
    assert only(run(inv(date_ambiguous=True)), "ambiguous_invoice_date").severity == Severity.warning


# 3 duplicates
def existing(**kw):
    base = dict(id="old", document_id=None, invoice_number="inv 1", supplier_name="ACME LIMITED",
                invoice_date=date(2025, 1, 5), total=D("1150"))
    base.update(kw)
    return ExistingInvoice(**base)


def test_exact_duplicate_detected_through_normalisation():
    f = only(run(inv(), existing=[existing()]), "duplicate_invoice")
    assert f.severity == Severity.error and "old" in f.actual


def test_same_number_different_values_is_conflict():
    f = only(run(inv(), existing=[existing(total=D("999"))]), "duplicate_invoice_number_conflict")
    assert "999.00" in f.description


def test_different_supplier_or_number_not_duplicate():
    assert not [f for f in run(inv(), existing=[existing(supplier_name="Other Co")]) if "duplicate" in f.rule_id]
    assert not [f for f in run(inv(), existing=[existing(invoice_number="INV-2")]) if "duplicate" in f.rule_id]


def test_invoice_does_not_duplicate_itself():
    assert not [f for f in run(inv(id="old"), existing=[existing()]) if "duplicate" in f.rule_id]


# 4 subtotal
def test_subtotal_matches_sum_of_lines():
    fs = run(inv(line_items=[line(1, 2, 100, 200), line(2, 8, 100, 800)]))
    assert "subtotal_mismatch" not in ids(fs)


def test_subtotal_mismatch():
    f = only(run(inv(line_items=[line(1, 2, 100, 200), line(2, 7, 100, 700)])), "subtotal_mismatch")
    assert (f.expected, f.actual) == ("900.00", "1000.00")


def test_subtotal_accepts_tax_inclusive_line_totals():
    lines = [line(1, 1, 500, 575, tax=75), line(2, 1, 500, 575, tax=75)]
    assert "subtotal_mismatch" not in ids(run(inv(line_items=lines)))


def test_unverifiable_subtotal_is_reported_not_skipped():
    assert "subtotal_unchecked" in ids(run(inv(line_items=[line(1, 1, 10, None)])))
    assert "subtotal_unchecked" in ids(run(inv()))


# 5 line totals
def test_line_total_mismatch():
    f = only(run(inv(line_items=[line(1, 3, 10, 35)])), "line_total_mismatch")
    assert (f.expected, f.actual, f.field) == ("30.00", "35.00", "line_items[1].total")


def test_line_total_may_include_line_tax():
    assert "line_total_mismatch" not in ids(run(inv(line_items=[line(1, 3, 10, 34.5, tax="4.5")])))


def test_fractional_quantities_exact():
    assert "line_total_mismatch" not in ids(run(inv(line_items=[line(1, "2.5", "19.99", "49.975")]),
                                                ValidationConfig(tolerance=D("0.01"))))


# 6 tax
def test_tax_vs_line_taxes():
    lines = [line(1, 1, 500, 500, tax=70), line(2, 1, 500, 500, tax=70)]
    assert only(run(inv(line_items=lines)), "tax_mismatch").expected == "140.00"
    ok = [line(1, 1, 500, 500, tax=75), line(2, 1, 500, 500, tax=75)]
    assert "tax_mismatch" not in ids(run(inv(line_items=ok)))


def test_tax_exceeding_subtotal_warns():
    assert only(run(inv(tax=D("2000"), total=D("3000"))), "tax_exceeds_subtotal").severity == Severity.warning


# 7 total: the spec's worked example
def test_spec_example_total_mismatch():
    f = only(run(inv(subtotal=D("1000"), tax=D("150"), total=D("1200"))), "total_mismatch")
    assert f.severity == Severity.error and (f.expected, f.actual) == ("1150.00", "1200.00")
    assert f.description and f.field == "total"


def test_total_within_tolerance_passes_and_beyond_fails():
    assert "total_mismatch" not in ids(run(inv(total=D("1150.01"))))
    assert "total_mismatch" in ids(run(inv(total=D("1150.02"))))
    assert "total_mismatch" not in ids(run(inv(total=D("1150.02")), ValidationConfig(tolerance=D("0.05"))))


def test_per_term_tolerance_for_rounded_lines():
    lines = [line(i, 1, "0.333", "0.33") for i in range(1, 4)]
    i = inv(subtotal=D("1.00"), tax=None, total=D("1.00"), line_items=lines)
    assert "subtotal_mismatch" in ids(run(i, ValidationConfig(tolerance=D("0"))))
    assert "subtotal_mismatch" not in ids(run(i, ValidationConfig(tolerance=D("0"), per_term_tolerance=D("0.005"))))


def test_no_float_error_accumulation():
    lines = [line(i, 1, "0.1", "0.1") for i in range(1, 11)]
    i = inv(subtotal=D("1.0"), tax=D("0"), total=D("1.0"), line_items=lines)
    acc = 0.0
    for _ in range(10):
        acc += 0.1
    assert acc != 1.0  # naive float accumulation gives 0.9999999999999999; this is what Decimal avoids
    assert ids(run(i, ValidationConfig(tolerance=D("0"))), Severity.error) == []  # Decimal is exact


def test_missing_tax_with_equal_total_is_fine_and_with_gap_warns():
    assert "total_unverifiable_tax_missing" not in ids(run(inv(tax=None, total=D("1000"))))
    assert "total_unverifiable_tax_missing" in ids(run(inv(tax=None, total=D("1150"))))


def test_missing_subtotal_reported():
    assert "total_unchecked" in ids(run(inv(subtotal=None)))


# 8 currency
def test_currency_rules():
    assert "currency_missing" in ids(run(inv(currency=None)))
    assert "currency_invalid" in ids(run(inv(currency="Taka")))
    assert "currency_unrecognized" in ids(run(inv(currency="ZZZ")))
    assert "currency_mixed" in ids(run(inv(detected_currencies={"BDT", "USD"})))
    assert "currency_conflict" in ids(run(inv(currency="BDT", detected_currencies={"USD"})))
    assert not [f for f in run(inv(currency="BDT", detected_currencies={"BDT"})) if f.rule_id.startswith("currency")]


# 9 numbers
def test_negative_and_invalid_numbers():
    assert only(run(inv(total=D("-5"))), "negative_amount").severity == Severity.warning
    strict = ValidationConfig(negative_severity=Severity.error)
    assert only(run(inv(total=D("-5")), strict), "negative_amount").severity == Severity.error
    assert "invalid_number" in ids(run(inv(total=D("NaN"))))
    assert "invalid_number" in ids(run(inv(tax=D("Infinity"))))
    assert "implausible_amount" in ids(run(inv(total=D("1e15"))))
    assert "zero_quantity" in ids(run(inv(line_items=[line(1, 0, 10, 0)])))
    assert "negative_amount" in ids(run(inv(line_items=[line(1, -1, 10, -10)])))


# engine behaviour
def test_findings_carry_source_references():
    refs = [{"page": 3, "text": "Total 1200"}]
    f = only(run(inv(total=D("1200"), field_refs={"total": refs})), "total_mismatch")
    assert {"page": 3, "text": "Total 1200"} in f.source_references


def test_crashing_rule_becomes_error_finding_and_others_still_run():
    def boom(i, c, x):
        raise RuntimeError("kaput")

    from app.validation.rules import RULES
    fs = validate_invoice(inv(total=D("1200")), rules=[("boom", boom), *RULES], today=TODAY)
    assert "rule_error" in ids(fs, Severity.error) and "total_mismatch" in ids(fs)
    assert has_blocking_errors(fs)


def test_results_sorted_errors_first():
    fs = run(inv(total=D("1200"), currency=None))
    sev = [f.severity for f in fs]
    assert sev == sorted(sev, key=lambda s: {Severity.error: 0, Severity.warning: 1, Severity.info: 2}[s])
