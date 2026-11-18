"""Record matching between two sets of invoices.

Stages, in order of confidence:
  A exact_supplier  canonical invoice number AND supplier equal        -> confirmed (auto)
  B exact_number    canonical invoice number equal, supplier differs   -> proposed
  C fuzzy           similar number + similar supplier + corroboration  -> proposed
  D attributes      one side has no invoice number: supplier+date+total -> proposed
Only stage A is ever auto-confirmed. Anything uncertain (several candidates, duplicate keys) is reported as
`ambiguous` and left for a human; nothing is merged on a guess.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from app.extraction.normalizers import invoice_key, name_key
from app.models.base import MatchStatus
from app.validation.config import ValidationConfig
from app.validation.types import InvoiceData


@dataclass(frozen=True, slots=True)
class Rec:
    inv: InvoiceData
    document_id: str
    label: str  # human-readable origin, e.g. "ledger.xlsx Invoices!4" or "inv.pdf pages 1-1"

    @property
    def id(self) -> str:
        return self.inv.id or ""

    @property
    def key(self) -> str | None:
        return invoice_key(self.inv.invoice_number)

    @property
    def skey(self) -> str | None:
        return name_key(self.inv.supplier_name)


@dataclass(frozen=True, slots=True)
class ReconcileConfig:
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    fuzzy_number_min: float = 0.8
    fuzzy_supplier_min: float = 0.85
    fuzzy_score_min: float = 0.8
    fuzzy_margin: float = 0.1  # best candidate must beat the runner-up by this much
    attributes_supplier_min: float = 0.9
    attributes_score: float = 0.8


@dataclass(slots=True)
class Pair:
    left: Rec
    right: Rec
    method: str
    score: float
    status: MatchStatus
    note: str | None = None


@dataclass(slots=True)
class MatchOutcome:
    pairs: list[Pair] = field(default_factory=list)
    unmatched_left: list[Rec] = field(default_factory=list)
    unmatched_right: list[Rec] = field(default_factory=list)
    duplicates_left: list[list[Rec]] = field(default_factory=list)
    duplicates_right: list[list[Rec]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)


def _ratio(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()


def _same_total(a: Rec, b: Rec, cfg: ReconcileConfig) -> bool:
    x, y = a.inv.total, b.inv.total
    return x is not None and y is not None and cfg.validation.within(x, y)


def _same_date(a: Rec, b: Rec) -> bool:
    return a.inv.invoice_date is not None and a.inv.invoice_date == b.inv.invoice_date


def _digits(key: str) -> str:
    return "".join(re.findall(r"\d+", key))


def _bucket(total: Decimal | None) -> int | None:
    return None if total is None else int(total // 1)


def duplicate_groups(recs: list[Rec]) -> list[list[Rec]]:
    """Records on one side that share canonical number AND supplier (never deleted, only reported)."""
    groups: dict[tuple[str, str], list[Rec]] = defaultdict(list)
    for r in recs:
        if r.key and r.skey:
            groups[(r.key, r.skey)].append(r)
    return [g for g in groups.values() if len(g) > 1]


def _labels(recs: list[Rec]) -> list[str]:
    return [r.label for r in recs]


def match_records(left: list[Rec], right: list[Rec], cfg: ReconcileConfig | None = None) -> MatchOutcome:
    cfg = cfg or ReconcileConfig()
    for r in (*left, *right):
        if not r.id:
            raise ValueError("every record needs a unique invoice id")
    out = MatchOutcome(duplicates_left=duplicate_groups(left), duplicates_right=duplicate_groups(right))
    rem_l = {r.id: r for r in left}
    rem_r = {r.id: r for r in right}

    def take(pair: Pair) -> None:
        out.pairs.append(pair)
        rem_l.pop(pair.left.id, None)
        rem_r.pop(pair.right.id, None)

    def park(reason: str, ls: list[Rec], rs: list[Rec], **extra: Any) -> None:
        out.ambiguous.append({"reason": reason, "left": _labels(ls), "right": _labels(rs),
                              "left_ids": [r.id for r in ls], "right_ids": [r.id for r in rs], **extra})
        for r in ls:
            rem_l.pop(r.id, None)
        for r in rs:
            rem_r.pop(r.id, None)

    # ---- A: exact number + supplier
    gl: dict[tuple[str, str], list[Rec]] = defaultdict(list)
    gr: dict[tuple[str, str], list[Rec]] = defaultdict(list)
    for r in left:
        if r.key and r.skey:
            gl[(r.key, r.skey)].append(r)
    for r in right:
        if r.key and r.skey:
            gr[(r.key, r.skey)].append(r)
    for gk in [k for k in gl if k in gr]:
        ls, rs = gl[gk], gr[gk]
        if len(ls) == 1 and len(rs) == 1:
            take(Pair(ls[0], rs[0], "exact_supplier", 1.0, MatchStatus.confirmed))
            continue
        used: set[str] = set()
        for a in ls:  # duplicate key: only pair records that are identical on date and total
            b = next((x for x in rs if x.id not in used and _same_date(a, x) and _same_total(a, x, cfg)), None)
            if b is not None:
                used.add(b.id)
                take(Pair(a, b, "exact_supplier", 0.9, MatchStatus.proposed,
                          "Duplicate invoice number on one side; paired by identical date and total"))
        rest_l = [x for x in ls if x.id in rem_l]
        rest_r = [x for x in rs if x.id in rem_r]
        if rest_l or rest_r:
            park("duplicate_key", rest_l, rest_r, key=gk[0])

    # ---- B: exact number only
    bl: dict[str, list[Rec]] = defaultdict(list)
    br: dict[str, list[Rec]] = defaultdict(list)
    for r in rem_l.values():
        if r.key:
            bl[r.key].append(r)
    for r in rem_r.values():
        if r.key:
            br[r.key].append(r)
    for k in [k for k in bl if k in br]:
        ls, rs = bl[k], br[k]
        if len(ls) == 1 and len(rs) == 1:
            take(Pair(ls[0], rs[0], "exact_number", 0.9, MatchStatus.proposed,
                      "Same invoice number but supplier differs or is missing"))
        else:
            park("duplicate_number", ls, rs, key=k)

    # ---- C: controlled fuzzy
    _fuzzy_stage(rem_l, rem_r, cfg, take, park)

    # ---- D: attributes (one side lacks an invoice number)
    _attribute_stage(rem_l, rem_r, cfg, take, park)

    out.unmatched_left = list(rem_l.values())
    out.unmatched_right = list(rem_r.values())
    return out


def _fuzzy_score(a: Rec, b: Rec, cfg: ReconcileConfig) -> float | None:
    if not a.key or not b.key:
        return None
    num = _ratio(a.key, b.key)
    sup = _ratio(a.skey, b.skey)
    if num < cfg.fuzzy_number_min or sup < cfg.fuzzy_supplier_min:
        return None
    total_eq, date_eq = _same_total(a, b, cfg), _same_date(a, b)
    if not (total_eq or date_eq):
        return None  # similarity alone is never enough: something else must corroborate
    if _digits(a.key) != _digits(b.key) and not (total_eq and date_eq):
        return None  # INV-0042 vs INV-0043 with different dates/amounts are different invoices
    score = 0.5 * num + 0.3 * sup + 0.1 * total_eq + 0.1 * date_eq
    return score if score >= cfg.fuzzy_score_min else None


def _candidates(rec: Rec, index_sup: dict, index_tot: dict) -> dict[str, Rec]:
    found: dict[str, Rec] = {}
    if rec.skey:
        for c in index_sup.get(rec.skey.split()[0], []):
            found[c.id] = c
    b = _bucket(rec.inv.total)
    if b is not None:
        for nb in (b - 1, b, b + 1):
            for c in index_tot.get(nb, []):
                found[c.id] = c
    return found


def _index(recs) -> tuple[dict, dict]:
    sup: dict[str, list[Rec]] = defaultdict(list)
    tot: dict[int, list[Rec]] = defaultdict(list)
    for r in recs:
        if r.skey:
            sup[r.skey.split()[0]].append(r)
        b = _bucket(r.inv.total)
        if b is not None:
            tot[b].append(r)
    return sup, tot


def _mutual_best(scored: dict[str, list[tuple[float, Rec]]], rem_l, rem_r, margin: float):
    """Pairs that are each other's unique best candidate; left-over contested records are returned as ambiguous."""
    best_l: dict[str, tuple[float, Rec, bool]] = {}
    for lid, cands in scored.items():
        cands.sort(key=lambda t: -t[0])
        top = cands[0]
        clear = len(cands) == 1 or top[0] - cands[1][0] >= margin
        best_l[lid] = (top[0], top[1], clear)
    by_right: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for lid, cands in scored.items():
        for score, rrec in cands:
            by_right[rrec.id].append((score, lid))
    pairs, contested = [], []
    for lid, (score, rrec, clear) in best_l.items():
        rivals = sorted(by_right[rrec.id], key=lambda t: -t[0])
        right_clear = len(rivals) == 1 or rivals[0][0] - rivals[1][0] >= margin
        if clear and right_clear and rivals[0][1] == lid:
            pairs.append((rem_l[lid], rrec, score))
        else:
            contested.append((rem_l[lid], [c[1] for c in scored[lid]]))
    return pairs, contested


def _fuzzy_stage(rem_l, rem_r, cfg: ReconcileConfig, take, park) -> None:
    sup, tot = _index(rem_r.values())
    scored: dict[str, list[tuple[float, Rec]]] = {}
    for lrec in list(rem_l.values()):
        if not lrec.key:
            continue
        cands = []
        for rrec in _candidates(lrec, sup, tot).values():
            s = _fuzzy_score(lrec, rrec, cfg)
            if s is not None:
                cands.append((s, rrec))
        if cands:
            scored[lrec.id] = cands
    if not scored:
        return
    pairs, contested = _mutual_best(scored, rem_l, rem_r, cfg.fuzzy_margin)
    for lrec, rrec, score in pairs:
        take(Pair(lrec, rrec, "fuzzy", round(score, 3), MatchStatus.proposed,
                  "Similar invoice number and supplier; confirm before relying on it"))
    for lrec, rcands in contested:
        if lrec.id in rem_l:
            park("multiple_fuzzy_candidates", [lrec], [c for c in rcands if c.id in rem_r])


def _attribute_stage(rem_l, rem_r, cfg: ReconcileConfig, take, park) -> None:
    index: dict[tuple[Any, int], list[Rec]] = defaultdict(list)
    for r in rem_r.values():
        b = _bucket(r.inv.total)
        if b is not None and r.inv.invoice_date is not None:
            index[(r.inv.invoice_date, b)].append(r)
    scored: dict[str, list[tuple[float, Rec]]] = {}
    for lrec in rem_l.values():
        b = _bucket(lrec.inv.total)
        if b is None or lrec.inv.invoice_date is None or not lrec.skey:
            continue
        cands = []
        for nb in (b - 1, b, b + 1):
            for rrec in index.get((lrec.inv.invoice_date, nb), []):
                if (lrec.key and rrec.key) or not rrec.skey:  # both numbered: numbers decide, not attributes
                    continue
                if _ratio(lrec.skey, rrec.skey) >= cfg.attributes_supplier_min and _same_total(lrec, rrec, cfg):
                    cands.append((cfg.attributes_score, rrec))
        if cands:
            scored[lrec.id] = cands
    if not scored:
        return
    pairs, contested = _mutual_best(scored, rem_l, rem_r, margin=1e-9)  # any tie is ambiguous
    for lrec, rrec, score in pairs:
        take(Pair(lrec, rrec, "attributes", score, MatchStatus.proposed,
                  "Matched on supplier, date and total because an invoice number is missing"))
    for lrec, rcands in contested:
        if lrec.id in rem_l:
            park("multiple_attribute_candidates", [lrec], [c for c in rcands if c.id in rem_r])
