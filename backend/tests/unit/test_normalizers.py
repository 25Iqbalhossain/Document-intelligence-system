from datetime import date
from decimal import Decimal as D

import pytest

from app.extraction.normalizers import (
    clean_invoice_number,
    invoice_key,
    name_key,
    normalize_currency,
    normalize_digits,
    parse_date,
    parse_money,
)


@pytest.mark.parametrize("text,value,cur", [
    ("1,150.00", D("1150.00"), None),
    ("৳ 1,23,456.50", D("123456.50"), "BDT"),          # lakh grouping
    ("Tk. 12,34,56,789", D("123456789"), "BDT"),        # crore grouping
    ("১১৫০", D("1150"), None),                           # Bengali digits
    ("৳১,২৩,৪৫৬.৫০", D("123456.50"), "BDT"),
    ("BDT 2,500", D("2500"), "BDT"),
    ("$1,000.50", D("1000.50"), "USD"),
    ("1.234,56", D("1234.56"), None),                    # European
    ("1.234.567", D("1234567"), None),
    ("12,5", D("12.5"), None),
    ("1,500", D("1500"), None),
    ("(1,000.00)", D("-1000.00"), None),                # accounting negative
    ("-250", D("-250"), None),
    ("250-", D("-250"), None),
    ("0", D("0"), None),
    ("0.00", D("0.00"), None),
    ("  99  ", D("99"), None),
])
def test_parse_money_ok(text, value, cur):
    r = parse_money(text)
    assert r.value == value and r.currency == cur


@pytest.mark.parametrize("text", [None, "", "   ", "abc", "N/A", "12.34.56,78", "1,2345", "--5", "1e5", "NaN", "৳"])
def test_parse_money_never_guesses(text):
    assert parse_money(text).value is None


def test_zero_is_not_missing():
    assert parse_money("0").value == D("0")
    assert parse_money("").value is None


def test_money_returns_decimal_exactly():
    assert parse_money("0.1").value + parse_money("0.2").value == D("0.3")


def test_money_warnings():
    assert "comma_as_decimal" in parse_money("12,5").warnings
    assert parse_money("1,500").warnings == ()


@pytest.mark.parametrize("text,expected,ambiguous", [
    ("2025-01-31", date(2025, 1, 31), False),
    ("31/01/2025", date(2025, 1, 31), False),
    ("31-01-2025", date(2025, 1, 31), False),
    ("31.01.2025", date(2025, 1, 31), False),
    ("01/31/2025", date(2025, 1, 31), False),            # only month-first is possible
    ("03/04/2025", date(2025, 4, 3), True),              # day-first default, flagged
    ("05/05/2025", date(2025, 5, 5), False),
    ("১৫/০১/২০২৫", date(2025, 1, 15), False),             # Bengali digits
    ("15 Jan 2025", date(2025, 1, 15), False),
    ("15-Jan-2025", date(2025, 1, 15), False),
    ("January 15, 2025", date(2025, 1, 15), False),
    ("15th January 2025", date(2025, 1, 15), False),
    ("Sept 3, 2024", date(2024, 9, 3), False),
    ("01/02/25", date(2025, 2, 1), True),
])
def test_parse_date_ok(text, expected, ambiguous):
    r = parse_date(text)
    assert r.value == expected and r.ambiguous is ambiguous


def test_month_first_option():
    r = parse_date("03/04/2025", day_first=False)
    assert r.value == date(2025, 3, 4) and r.ambiguous


def test_two_digit_year_flagged():
    assert "two_digit_year" in parse_date("15/01/25").warnings
    assert parse_date("15/01/99").value == date(1999, 1, 15)


@pytest.mark.parametrize("text", [None, "", "31/02/2025", "2025-13-01", "tomorrow", "00/00/0000", "32 Jan 2025"])
def test_parse_date_rejects_invalid(text):
    assert parse_date(text).value is None


def test_currency():
    assert [normalize_currency(x) for x in ["৳", "Tk", "tk.", "BDT", "usd", "$", "€", "Rs", "XYZ", None, ""]] == [
        "BDT", "BDT", "BDT", "BDT", "USD", "USD", "EUR", None, None, None, None]


def test_digits():
    assert normalize_digits("১২৩ ٤٥٦ 789") == "123 456 789"


def test_keys():
    assert invoice_key("Inv-001") == invoice_key("INV 001") == invoice_key("inv/001") == "INV001"
    assert invoice_key("ইনভয়েস-১২") is not None
    assert invoice_key("---") is None and invoice_key(None) is None
    assert clean_invoice_number("Invoice No: INV-001 ") == "INV-001"
    assert clean_invoice_number("#  42") == "42"
    assert name_key("Rahim Traders Ltd.") == name_key("RAHIM TRADERS LIMITED") == "rahim traders"
    assert name_key("  ") is None


@pytest.mark.parametrize("text", ["1,23,45,6", "12,3456,789", "1,2,3", "1.23.456.7"])
def test_irregular_grouping_rejected(text):
    assert parse_money(text).value is None


@pytest.mark.parametrize("text,value", [("1,23,45,678", D("12345678")), ("12,34,567.89", D("1234567.89")),
                                        ("1,234,567.89", D("1234567.89")), ("1.234.567,89", D("1234567.89"))])
def test_regular_grouping_accepted(text, value):
    assert parse_money(text).value == value
