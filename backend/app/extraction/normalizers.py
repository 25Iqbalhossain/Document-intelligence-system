"""Deterministic parsing of numbers, dates, currencies and identifiers from extracted text.

The LLM only *extracts* strings; these functions decide what they mean. Anything that cannot be
parsed returns None (never a guess), and anything that could be read two ways is flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

_BENGALI = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_EXT_ARABIC_INDIC = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def normalize_digits(text: str) -> str:
    """Map Bengali and Arabic-Indic digits to ASCII."""
    return text.translate(_BENGALI).translate(_ARABIC_INDIC).translate(_EXT_ARABIC_INDIC)


# ---------------------------------------------------------------- currency

_CURRENCY_SYMBOLS = {"৳": "BDT", "$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_CURRENCY_WORDS = {
    "bdt": "BDT", "tk": "BDT", "taka": "BDT", "usd": "USD", "us$": "USD", "eur": "EUR", "gbp": "GBP", "inr": "INR",
    "aed": "AED", "sar": "SAR", "cny": "CNY", "jpy": "JPY", "myr": "MYR", "sgd": "SGD",
}
KNOWN_CURRENCIES = frozenset(_CURRENCY_WORDS.values()) | frozenset(_CURRENCY_SYMBOLS.values())
# "Rs" is deliberately absent: it means INR, PKR or LKR depending on the document.
_WORD_RE = re.compile(r"(?<![A-Za-z])(us\$|bdt|tk|taka|usd|eur|gbp|inr|aed|sar|cny|jpy|myr|sgd)\.?(?![A-Za-z])", re.I)


def normalize_currency(text: str | None) -> str | None:
    """Return an ISO code for a symbol/word/code, or None when unknown or ambiguous."""
    if not text:
        return None
    t = text.strip()
    if t in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[t]
    m = _WORD_RE.fullmatch(t)
    if m:
        return _CURRENCY_WORDS[m.group(1).lower()]
    up = t.upper()
    return up if up in KNOWN_CURRENCIES else None


# ---------------------------------------------------------------- money

@dataclass(frozen=True, slots=True)
class MoneyParse:
    value: Decimal | None
    currency: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _valid_grouping(integer_part: str, sep: str) -> bool:
    """Thousands separators must follow Western (1,234,567) or lakh/crore (12,34,567) grouping."""
    e = re.escape(sep)
    return bool(re.fullmatch(rf"\d{{1,3}}(?:{e}\d{{3}})+", integer_part)
                or re.fullmatch(rf"\d{{1,2}}(?:{e}\d{{2}})*{e}\d{{3}}", integer_part))


def parse_money(text: str | None) -> MoneyParse:
    """Parse '৳ 1,23,456.50', '1.234,56 EUR', '(1,000.00)', '১১৫০' ... Returns value=None if unsure."""
    if text is None or not str(text).strip():
        return MoneyParse(None)
    s = normalize_digits(str(text)).strip().replace("−", "-")  # unicode minus
    currency = None
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in s:
            currency = code
            s = s.replace(sym, " ")
    m = _WORD_RE.search(s)
    if m:
        currency = currency or _CURRENCY_WORDS[m.group(1).lower()]
        s = _WORD_RE.sub(" ", s)

    negative = False
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    s = s.replace(" ", "").replace("'", "").replace(" ", "")
    if s.startswith("-"):
        negative, s = True, s[1:]
    elif s.endswith("-"):
        negative, s = True, s[:-1]
    elif s.startswith("+"):
        s = s[1:]

    if not s or not re.fullmatch(r"[0-9.,]+", s) or not re.search(r"[0-9]", s):
        return MoneyParse(None, currency, ("unparseable",))

    warnings: list[str] = []
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        if s.count(dec) > 1 or not _valid_grouping(s.split(dec)[0], thou):
            return MoneyParse(None, currency, ("unparseable",))
        if dec == ",":
            warnings.append("comma_as_decimal")
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        if s.count(",") > 1:  # 1,234,567 or lakh grouping 1,23,456
            if not _valid_grouping(s, ","):
                return MoneyParse(None, currency, ("unparseable",))
            s = s.replace(",", "")
        else:
            tail = s.split(",")[1]
            if len(tail) == 3:
                s = s.replace(",", "")  # 1,500 -> thousands
            elif 1 <= len(tail) <= 2:
                s = s.replace(",", ".")
                warnings.append("comma_as_decimal")
            else:
                return MoneyParse(None, currency, ("unparseable",))
    elif "." in s and s.count(".") > 1:
        if not _valid_grouping(s, "."):
            return MoneyParse(None, currency, ("unparseable",))
        s = s.replace(".", "")  # 1.234.567 (dot thousands)
        warnings.append("dot_as_thousands")

    try:
        value = Decimal(s)
    except InvalidOperation:
        return MoneyParse(None, currency, ("unparseable",))
    if value.is_nan() or value.is_infinite():
        return MoneyParse(None, currency, ("unparseable",))
    return MoneyParse(-value if negative else value, currency, tuple(warnings))


# ---------------------------------------------------------------- dates

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))


@dataclass(frozen=True, slots=True)
class DateParse:
    value: date | None
    ambiguous: bool = False  # both day-first and month-first readings are valid and differ
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _make(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _year(raw: str) -> tuple[int, list[str]]:
    y = int(raw)
    if len(raw) == 2:
        return (2000 + y if y <= 69 else 1900 + y), ["two_digit_year"]
    return y, []


def parse_date(text: str | None, day_first: bool = True) -> DateParse:
    """Parse common invoice date formats. Defaults to day-first (Bangladesh/UK); flags ambiguity."""
    if text is None or not str(text).strip():
        return DateParse(None)
    s = normalize_digits(str(text)).strip().strip(".,")
    s = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", s, flags=re.I)

    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        d = _make(int(m[1]), int(m[2]), int(m[3]))
        return DateParse(d, False, () if d else ("invalid_calendar_date",))

    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})", s)
    if m:
        a, b = int(m[1]), int(m[2])
        year, warns = _year(m[3])
        first, second = (a, b) if day_first else (b, a)  # (day, month) under the configured order
        if first > 12 and second <= 12:
            day, month, ambiguous = first, second, False
        elif second > 12 and first <= 12:
            day, month, ambiguous = second, first, False  # only the other order is possible
        else:
            day, month, ambiguous = first, second, (a != b and a <= 12 and b <= 12)
        d = _make(year, month, day)
        if d is None:
            return DateParse(None, False, (*warns, "invalid_calendar_date"))
        return DateParse(d, ambiguous, tuple(warns + (["ambiguous_day_month"] if ambiguous else [])))

    m = re.fullmatch(rf"(\d{{1,2}})[\s\-/.,]*({_MONTH_RE})[\s\-/.,]*(\d{{4}}|\d{{2}})", s, re.I)
    if m:
        year, warns = _year(m[3])
        d = _make(year, _MONTHS[m[2].lower()], int(m[1]))
        return DateParse(d, False, tuple(warns) if d else (*warns, "invalid_calendar_date"))

    m = re.fullmatch(rf"({_MONTH_RE})[\s\-/.,]*(\d{{1,2}})[\s\-/.,]+(\d{{4}}|\d{{2}})", s, re.I)
    if m:
        year, warns = _year(m[3])
        d = _make(year, _MONTHS[m[1].lower()], int(m[2]))
        return DateParse(d, False, tuple(warns) if d else (*warns, "invalid_calendar_date"))

    return DateParse(None, False, ("unparseable",))


# ---------------------------------------------------------------- identifiers

_COMPANY_SUFFIXES = {"ltd", "limited", "pvt", "private", "inc", "incorporated", "co", "company", "llc", "plc", "corp",
                     "corporation", "enterprise", "enterprises"}


def invoice_key(number: str | None) -> str | None:
    """Canonical form for matching: ASCII digits, upper-case, alphanumerics only. 'Inv-001' == 'INV 001'."""
    if number is None:
        return None
    key = re.sub(r"[^A-Z0-9]", "", normalize_digits(number).upper())
    return key or None


def clean_invoice_number(number: str | None) -> str | None:
    """Display form: trimmed, whitespace collapsed, leading 'No.'/'#' labels removed. Case is preserved."""
    if number is None:
        return None
    s = re.sub(r"^(invoice\s*)?(no\.?|number|#)\s*[:.\-]?\s*", "", normalize_digits(number).strip(), flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def name_key(name: str | None) -> str | None:
    """Canonical company-name form for comparison (case, punctuation, legal suffixes ignored)."""
    if name is None:
        return None
    words = re.sub(r"[^\w\s]", " ", normalize_digits(name).casefold()).split()
    words = [w for w in words if w not in _COMPANY_SUFFIXES]
    return " ".join(words) or None


def detect_currencies(text: str) -> set[str]:
    """Currencies mentioned in a text span (symbols and codes). Used as a cross-check hint, never as a value."""
    found = {code for sym, code in _CURRENCY_SYMBOLS.items() if sym in text}
    found |= {_CURRENCY_WORDS[m.group(1).lower()] for m in _WORD_RE.finditer(text)}
    return found
