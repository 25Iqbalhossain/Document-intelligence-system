"""Cross-document reconciliation: persist matches, discrepancies and a report. Never mutates invoices."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentRelationship, Invoice, ReconciliationRun
from app.models.base import MatchStatus
from app.models.invoices import RelationType
from app.reconciliation.comparator import compare_records
from app.reconciliation.matcher import MatchOutcome, Rec, ReconcileConfig, match_records
from app.services.validation_service import invoice_to_data


class ReconciliationError(Exception):
    pass


def _label(doc: Document, inv: Invoice) -> str:
    """Human-readable origin of a record: file plus cell (spreadsheets) or page (PDF/image)."""
    refs = [r for r in (inv.source_references or []) if isinstance(r, dict)]
    ref = next((r for r in refs if r.get("cell")), None) or next((r for r in refs if r.get("page")), None)
    where = ""
    if ref and ref.get("cell"):
        where = f" {ref.get('sheet') or ''}!{ref['cell']}".replace(" !", " ")
    elif ref and ref.get("page"):
        where = f" p.{ref['page']}"
    if inv.invoice_number:
        where += f" ({inv.invoice_number})"
    return f"{doc.original_filename}{where}"


def _load(session: Session, doc_ids: list[uuid.UUID]) -> list[Rec]:
    recs: list[Rec] = []
    for did in doc_ids:
        doc = session.get(Document, did)
        if doc is None or doc.deleted_at is not None:
            raise ReconciliationError(f"Document {did} not found")
        invs = session.scalars(select(Invoice).where(Invoice.document_id == did, Invoice.deleted_at.is_(None)))
        recs.extend(Rec(invoice_to_data(i), str(did), _label(doc, i)) for i in invs)
    return recs


def _summary(out: MatchOutcome, n_left: int, n_right: int) -> dict[str, Any]:
    def rows(rs): return [{"id": r.id, "document_id": r.document_id, "label": r.label} for r in rs]
    return {
        "left_count": n_left, "right_count": n_right,
        "matched": len(out.pairs),
        "confirmed": sum(p.status == MatchStatus.confirmed for p in out.pairs),
        "proposed": sum(p.status == MatchStatus.proposed for p in out.pairs),
        "unmatched_left": rows(out.unmatched_left), "unmatched_right": rows(out.unmatched_right),
        "duplicates_left": [rows(g) for g in out.duplicates_left],
        "duplicates_right": [rows(g) for g in out.duplicates_right],
        "ambiguous": out.ambiguous,
    }


def run_reconciliation(
    session: Session, left_document_ids: list[uuid.UUID], right_document_ids: list[uuid.UUID],
    *, user_id: uuid.UUID | None = None, cfg: ReconcileConfig | None = None,
) -> ReconciliationRun:
    if not left_document_ids or not right_document_ids:
        raise ReconciliationError("Both sides need at least one document")
    if set(left_document_ids) & set(right_document_ids):
        raise ReconciliationError("A document cannot be on both sides")
    cfg = cfg or ReconcileConfig()
    left, right = _load(session, left_document_ids), _load(session, right_document_ids)
    out = match_records(left, right, cfg)
    run = ReconciliationRun(
        left_document_ids=[str(i) for i in left_document_ids], right_document_ids=[str(i) for i in right_document_ids],
        config={"tolerance": str(cfg.validation.tolerance), "fuzzy_number_min": cfg.fuzzy_number_min,
                "fuzzy_supplier_min": cfg.fuzzy_supplier_min, "fuzzy_margin": cfg.fuzzy_margin},
        summary=_summary(out, len(left), len(right)), created_by=user_id,
    )
    session.add(run)
    session.flush()
    for p in out.pairs:
        session.add(DocumentRelationship(
            source_document_id=uuid.UUID(p.left.document_id), target_document_id=uuid.UUID(p.right.document_id),
            source_invoice_id=uuid.UUID(p.left.id), target_invoice_id=uuid.UUID(p.right.id),
            relation_type=RelationType.reconciles_with, match_method=p.method, match_score=p.score,
            status=p.status, run_id=run.id,
            discrepancies=_discrepancies(p, cfg),
        ))
    for side in (out.duplicates_left, out.duplicates_right):  # flagged, never deleted or merged
        for group in side:
            first = group[0]
            for other in group[1:]:
                session.add(DocumentRelationship(
                    source_document_id=uuid.UUID(other.document_id), target_document_id=uuid.UUID(first.document_id),
                    source_invoice_id=uuid.UUID(other.id), target_invoice_id=uuid.UUID(first.id),
                    relation_type=RelationType.duplicate_of, match_method="duplicate", match_score=1.0,
                    status=MatchStatus.proposed, run_id=run.id, discrepancies=[],
                ))
    session.flush()
    return run


def _discrepancies(p, cfg: ReconcileConfig) -> list[dict[str, Any]]:
    return [d.to_dict() for d in compare_records(p.left.inv, p.right.inv, cfg.validation)]


def build_report(session: Session, run_id: uuid.UUID) -> dict[str, Any]:
    run = session.get(ReconciliationRun, run_id)
    if run is None:
        raise ReconciliationError("Run not found")
    rels = session.scalars(select(DocumentRelationship).where(DocumentRelationship.run_id == run_id)
                           .order_by(DocumentRelationship.created_at, DocumentRelationship.id)).all()
    matches = []
    for r in rels:
        if r.relation_type != RelationType.reconciles_with:
            continue
        errs = [d for d in r.discrepancies if d.get("severity") == "error"]
        warns = [d for d in r.discrepancies if d.get("severity") == "warning"]
        matches.append({
            "id": str(r.id), "left_invoice_id": str(r.source_invoice_id), "right_invoice_id": str(r.target_invoice_id),
            "method": r.match_method, "score": r.match_score, "status": r.status.value,
            "errors": len(errs), "warnings": len(warns), "discrepancies": r.discrepancies,
            # a match with no discrepancies is only "reconciled" once the match itself is confirmed
            "outcome": ("rejected" if r.status == MatchStatus.rejected else
                        "discrepancies" if errs or warns else
                        "reconciled" if r.status == MatchStatus.confirmed else "needs_confirmation"),
        })
    return {"run_id": str(run.id), "created_at": run.created_at.isoformat(), "summary": run.summary, "matches": matches}


def _safe(v: Any) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # spreadsheet formula-injection guard


def report_to_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["row_type", "left_invoice_id", "right_invoice_id", "method", "status", "field", "kind", "severity",
                "left_value", "right_value", "description"])
    for m in report["matches"]:
        base = ["match", m["left_invoice_id"], m["right_invoice_id"], m["method"], m["status"]]
        if not m["discrepancies"]:
            w.writerow([*map(_safe, base), "", m["outcome"], "", "", "", ""])
        for d in m["discrepancies"]:
            w.writerow([*map(_safe, base), *map(_safe, (d["field"], d["kind"], d["severity"], d["left"], d["right"],
                                                       d["description"]))])
    s = report["summary"]
    for side, key in (("unmatched_left", "unmatched_left"), ("unmatched_right", "unmatched_right")):
        for r in s[key]:
            w.writerow([side, r["id"] if side == "unmatched_left" else "", r["id"] if side == "unmatched_right" else "",
                        "", "", "", "unmatched", "warning", "", "", _safe(r["label"])])
    return buf.getvalue()


def _decide(
    session: Session, rel_id: uuid.UUID, status: MatchStatus, user_id: uuid.UUID | None
) -> DocumentRelationship:
    rel = session.get(DocumentRelationship, rel_id)
    if rel is None:
        raise ReconciliationError("Relationship not found")
    rel.status, rel.confirmed_by = status, user_id
    session.flush()
    return rel


def confirm_match(session: Session, rel_id: uuid.UUID, user_id: uuid.UUID | None = None) -> DocumentRelationship:
    return _decide(session, rel_id, MatchStatus.confirmed, user_id)


def reject_match(session: Session, rel_id: uuid.UUID, user_id: uuid.UUID | None = None) -> DocumentRelationship:
    return _decide(session, rel_id, MatchStatus.rejected, user_id)
