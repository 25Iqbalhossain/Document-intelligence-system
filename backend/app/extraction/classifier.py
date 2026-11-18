"""Cheap, deterministic document classification used to avoid LLM calls on clearly non-invoice text.

Conservative by design: only text with essentially no invoice vocabulary is called 'other'. Borderline text is
'unknown' and still goes through extraction; both 'other' and 'unknown' end up with a human.
"""

from __future__ import annotations

import re

from app.models.base import DocumentType

_TERMS: tuple[tuple[str, float], ...] = (
    (r"\btax invoice\b", 2), (r"\binvoice\b", 2), (r"\binvoice\s*(no|number|#)", 2), (r"\bcredit note\b", 2),
    (r"\bbill to\b", 1), (r"\bsub\s*-?total\b", 1), (r"\bvat\b", 1), (r"\bgrand total\b", 1), (r"\btotal\b", 0.5),
    (r"\bdue date\b", 1), (r"\bunit price\b", 1), (r"\bquantity\b|\bqty\b", 0.5), (r"\bbin\b", 0.5),
    (r"ইনভয়েস", 2), (r"চালান", 2), (r"মূসক", 1), (r"মোট", 0.5),
)
INVOICE_SCORE = 4.0
UNKNOWN_SCORE = 2.0
_SAMPLE_CHARS = 20000


def classify_text(text: str) -> tuple[DocumentType, float]:
    sample = text[:_SAMPLE_CHARS].casefold()
    score = sum(w for pat, w in _TERMS if re.search(pat, sample))
    if score >= INVOICE_SCORE:
        return DocumentType.invoice, round(min(1.0, 0.5 + score / 16), 2)
    if score >= UNKNOWN_SCORE:
        return DocumentType.unknown, round(0.3 + score / 20, 2)
    return DocumentType.other, 0.7
