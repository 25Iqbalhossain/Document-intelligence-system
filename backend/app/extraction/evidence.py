"""Evidence verification: a value is only trusted if its quoted evidence really appears in the source.

This is the main control against hallucinated values. The LLM must quote the text it relied on; we then
check (1) the quote exists in the cited page (or another page of the window), and (2) the value is
actually contained in that quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.extraction.normalizers import (
    detect_currencies,
    normalize_currency,
    normalize_digits,
    parse_date,
    parse_money,
)
from app.extraction.schemas import NUMBER_FIELDS

APPROX_THRESHOLD = 0.85  # fuzzy match accepted as "approximate" (OCR noise, whitespace, ligatures)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_digits(text).casefold()).strip()


@dataclass(frozen=True, slots=True)
class Located:
    page: int | None
    score: float  # 1.0 = exact normalised substring, <1.0 = fuzzy similarity, 0.0 = not found
    exact: bool
    matched_text: str | None
    page_corrected: bool = False  # the quote was on a different page than the model claimed


def _fuzzy_best(evidence_n: str, page_text: str) -> tuple[float, str | None]:
    """Best similarity between the evidence and any run of consecutive lines of the page."""
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    if not lines:
        return 0.0, None
    span = max(1, evidence_n.count("\n") + 1, len(evidence_n) // 60 + 1)
    best, best_text = 0.0, None
    for size in {1, span, span + 1}:
        for i in range(0, max(len(lines) - size + 1, 1)):
            chunk = " ".join(lines[i:i + size])
            cn = norm(chunk)
            if abs(len(cn) - len(evidence_n)) > max(len(evidence_n), 20):
                continue
            sm = SequenceMatcher(None, evidence_n, cn, autojunk=False)
            ratio = sm.ratio()
            if len(evidence_n) < len(cn):  # the evidence may be only a part of a longer line
                m = sm.find_longest_match(0, len(evidence_n), 0, len(cn))
                if m.size >= 0.9 * len(evidence_n):
                    ratio = max(ratio, m.size / len(evidence_n))
            if ratio > best:
                best, best_text = ratio, chunk
    return best, best_text


def locate_evidence(evidence: str, pages: dict[int, str], cited_page: int | None) -> Located:
    ev = norm(evidence)
    if not ev:
        return Located(None, 0.0, False, None)
    order = ([cited_page] if cited_page in pages else []) + [p for p in pages if p != cited_page]
    for p in order:  # exact match: cited page first, then the rest of the window
        if ev in norm(pages[p]):
            return Located(p, 1.0, True, evidence, page_corrected=cited_page is not None and p != cited_page)
    best = Located(None, 0.0, False, None)
    for p in order:
        score, text = _fuzzy_best(ev, pages[p])
        if score > best.score:
            best = Located(p, score, False, text, page_corrected=cited_page is not None and p != cited_page)
    return best if best.score >= APPROX_THRESHOLD else Located(None, best.score, False, None)


_DATE_TOKENS = re.compile(
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|\d{1,2}(?:st|nd|rd|th)?[\s\-/.,]*[A-Za-z]{3,9}[\s\-/.,]*\d{2,4}|[A-Za-z]{3,9}[\s\-/.]*\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}",
    re.I,
)
_NUM_TOKENS = re.compile(r"\d[\d,.']*\d|\d")


def value_supported(field_name: str, value: str, evidence: str) -> bool:
    """Is `value` actually present in `evidence`? Numbers and dates compare by meaning, text by content."""
    ev_n, val_n = norm(evidence), norm(value)
    if field_name in NUMBER_FIELDS:
        target = parse_money(value).value
        if target is None:
            return False
        return any(parse_money(tok).value == target for tok in _NUM_TOKENS.findall(normalize_digits(evidence)))
    if field_name == "invoice_date":
        if val_n in ev_n:
            return True
        target = parse_date(value).value
        return target is not None and any(parse_date(t).value == target for t in _DATE_TOKENS.findall(evidence))
    if field_name == "currency":
        code = normalize_currency(value)
        return val_n in ev_n or (code is not None and code in detect_currencies(evidence))
    return val_n in ev_n  # names, invoice numbers, descriptions
