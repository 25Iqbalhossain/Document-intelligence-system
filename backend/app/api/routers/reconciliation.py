from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_document_for, require_role
from app.core.database import get_db
from app.models import DocumentRelationship, ReconciliationRun, User
from app.models.base import UserRole
from app.services.reconciliation_service import (
    ReconciliationError,
    build_report,
    confirm_match,
    reject_match,
    report_to_csv,
    run_reconciliation,
)

router = APIRouter(tags=["reconciliation"])
REVIEWERS = (UserRole.admin, UserRole.reviewer)


class RunIn(BaseModel):
    left_document_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    right_document_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


@router.post("/reconciliation", status_code=201)
def create_run(body: RunIn, db: Session = Depends(get_db), user: User = Depends(require_role(*REVIEWERS))):
    for did in (*body.left_document_ids, *body.right_document_ids):
        get_document_for(db, user, did)
    try:
        run = run_reconciliation(db, body.left_document_ids, body.right_document_ids, user_id=user.id)
        db.commit()
    except ReconciliationError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    return build_report(db, run.id)


def _report(db: Session, run_id: uuid.UUID):
    if db.get(ReconciliationRun, run_id) is None:
        raise HTTPException(404, "Run not found")
    return build_report(db, run_id)


@router.get("/reconciliation/{run_id}")
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_role(*REVIEWERS))):
    return _report(db, run_id)


@router.get("/reconciliation/{run_id}/csv")
def get_run_csv(run_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_role(*REVIEWERS))):
    return Response(report_to_csv(_report(db, run_id)), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="reconciliation-{run_id}.csv"'})


def _decide(db: Session, rel_id: uuid.UUID, user: User, fn):
    if db.get(DocumentRelationship, rel_id) is None:
        raise HTTPException(404, "Relationship not found")
    rel = fn(db, rel_id, user.id)
    db.commit()
    return {"id": str(rel.id), "status": rel.status.value}


@router.post("/relationships/{rel_id}/confirm")
def confirm(rel_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(*REVIEWERS))):
    return _decide(db, rel_id, user, confirm_match)


@router.post("/relationships/{rel_id}/reject")
def reject(rel_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(*REVIEWERS))):
    return _decide(db, rel_id, user, reject_match)
