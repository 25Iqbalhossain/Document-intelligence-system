"""End-to-end check: real Celery worker + Redis + PostgreSQL + real Tesseract.

Requires running Postgres (migrated) and Redis. Creates temporary rows and removes them afterwards.
Usage: DOCINT_DATABASE_URL=... DOCINT_REDIS_URL=... python scripts/e2e_worker_check.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path[:0] = [str(BACKEND), str(ROOT / "backend")]


def main() -> int:
    store = tempfile.mkdtemp(prefix="docint-e2e-")
    os.environ["DOCINT_STORAGE_ROOT"] = store
    from sqlalchemy import select, text

    from app.core.database import get_session_factory
    from app.models import Document, DocumentPage, User
    from app.services import orchestration as orch
    from app.services.documents import ingest_upload
    from app.services.storage import LocalStorage
    from app.workers.tasks import celery_publisher
    from tests.unit import factories as f

    work = Path(tempfile.mkdtemp(prefix="docint-e2e-src-"))
    files = {
        "digital120": f.digital_pdf(work / "digital120.pdf", [f"Page {i} text with enough characters" for i in range(1, 121)]),
        "scanned": f.scanned_pdf(work / "scanned.pdf", ["Invoice Number INV-2025-0042", "Total 1150.00 BDT"], extra_blank=True),
        "xlsx": f.xlsx(work / "book.xlsx"),
    }
    worker = subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "app.workers.celery_app", "worker", "-Q", "default,ocr",
         "-c", "2", "--loglevel=WARNING", "--without-gossip", "--without-mingle"],
        cwd=BACKEND, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(4)
    Session = get_session_factory()
    ids: dict[str, uuid.UUID] = {}
    user_id = None
    try:
        with Session() as s:
            user = User(email=f"e2e-{uuid.uuid4().hex[:6]}@example.com", password_hash="x")
            s.add(user)
            s.flush()
            user_id = user.id
            storage = LocalStorage(store)
            for name, path in files.items():
                with path.open("rb") as fh:
                    ids[name] = ingest_upload(s, storage, owner_id=user.id, filename=path.name, stream=fh).document.id
            s.commit()
            t0 = time.perf_counter()
            orch.relay_outbox(s, celery_publisher)  # first hop: the outbox relay; the worker handles the rest

        deadline = time.time() + 180
        final: dict[str, str] = {}
        while time.time() < deadline and len(final) < len(ids):
            time.sleep(1)
            with Session() as s:
                for name, did in ids.items():
                    st = s.get(Document, did).status.value
                    if st in {"processed", "needs_review", "failed", "cancelled"}:
                        final[name] = st
        elapsed = time.perf_counter() - t0
        print(f"finished {len(final)}/{len(ids)} documents in {elapsed:.1f}s: {final}")
        # With the default mock LLM nothing is fabricated, so documents end in needs_review, never failed.
        ok = all(v in {"processed", "needs_review"} for v in final.values()) and len(final) == len(ids)
        with Session() as s:
            for name, did in ids.items():
                p = orch.progress(s, did)
                d = s.get(Document, did)
                print(f"  {name}: {p} doc_type={d.doc_type.value} reasons={(d.metadata_ or {}).get('review_reasons')}")
            sc = s.scalars(select(DocumentPage).where(DocumentPage.document_id == ids["scanned"])
                           .order_by(DocumentPage.page_number)).all()
            print("  scanned page 1 ocr_confidence:", sc[0].ocr_confidence, "| status:", [p.status for p in sc])
            hit = s.execute(text(
                "select count(*) from extracted_text where document_id=:d and text ilike '%INV-2025-0042%'"
            ), {"d": ids["scanned"]}).scalar()
            print("  OCR text contains invoice number:", bool(hit))
            ok = ok and bool(hit)
        return 0 if ok else 1
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
        log = (worker.stdout.read() if worker.stdout else "").strip()
        if log:
            print("--- worker log (WARNING+) ---\n" + log[-1500:])
        with Session() as s:
            if user_id:
                s.execute(text("delete from documents where owner_id=:u"), {"u": user_id})
                s.execute(text("delete from users where id=:u"), {"u": user_id})
                s.commit()


if __name__ == "__main__":
    raise SystemExit(main())
