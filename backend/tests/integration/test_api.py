import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DOCINT_SECRET_KEY", "test-secret-key-that-is-long-enough-123456")

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Invoice, InvoiceLineItem, ManualCorrection, User, ValidationResult  # noqa: E402
from app.models.base import ResolutionStatus, ReviewStatus, UserRole  # noqa: E402
from app.services.storage import LocalStorage  # noqa: E402
from app.services.validation_service import run_validation  # noqa: E402
from tests.integration.test_schema import make_doc  # noqa: E402
from tests.integration.test_validation_service import make_invoice  # noqa: E402

PW = "correct-horse-battery"


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setattr("app.api.routers.documents._publish", lambda _db: None)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    from app.api.deps import get_storage
    app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)
    app.dependency_overrides[get_settings] = lambda: Settings(storage_root=str(tmp_path))
    return TestClient(app)


def mkuser(db, role):
    u = User(email=f"{uuid.uuid4().hex[:10]}@example.com", password_hash=hash_password(PW), role=role)
    db.add(u)
    db.flush()
    return u


def login(client, user):
    r = client.post("/api/v1/auth/login", json={"email": user.email, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()


def hdr(client, user):
    return {"Authorization": f"Bearer {login(client, user)['access_token']}"}


CSV = b"Invoice No,Date,Supplier,Total\nINV-1,2025-01-05,ACME Ltd,100.00\n"


def test_auth_required_and_login_failures(client, db):
    assert client.get("/api/v1/documents").status_code == 401
    u = mkuser(db, UserRole.viewer)
    assert client.post("/api/v1/auth/login", json={"email": u.email, "password": "nope"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": PW}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=hdr(client, u)).json()["email"] == u.email


def test_token_tampering_and_expiry_rejected(client, db):
    u = mkuser(db, UserRole.viewer)
    key = get_settings().secret_key.get_secret_value()
    expired = jwt.encode({"sub": str(u.id), "typ": "access", "exp": datetime.now(UTC) - timedelta(minutes=1)}, key,
                         algorithm="HS256")
    forged = jwt.encode({"sub": str(u.id), "typ": "access", "exp": datetime.now(UTC) + timedelta(hours=1)},
                        "wrong-key-wrong-key-wrong-key-wrong-key", algorithm="HS256")
    for t in (expired, forged, "garbage"):
        assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_refresh_rotation_and_reuse_detection(client, db):
    u = mkuser(db, UserRole.viewer)
    t1 = login(client, u)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 200
    t2 = r.json()
    # replaying the used token revokes the whole family, including the fresh one
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": t2["refresh_token"]}).status_code == 401


def test_rbac_and_ownership(client, db):
    viewer, up1, up2, rev = (mkuser(db, r) for r in (UserRole.viewer, UserRole.uploader, UserRole.uploader,
                                                      UserRole.reviewer))
    assert client.post("/api/v1/documents", headers=hdr(client, viewer),
                       files={"file": ("a.csv", CSV)}).status_code == 403
    r = client.post("/api/v1/documents", headers=hdr(client, up1), files={"file": ("a.csv", CSV)})
    assert r.status_code == 201, r.text
    did = r.json()["document"]["id"]
    assert client.get(f"/api/v1/documents/{did}", headers=hdr(client, up1)).status_code == 200
    assert client.get(f"/api/v1/documents/{did}", headers=hdr(client, up2)).status_code == 404  # not 403: no probing
    assert client.get(f"/api/v1/documents/{did}", headers=hdr(client, rev)).status_code == 200
    assert client.get("/api/v1/documents", headers=hdr(client, up2)).json() == []
    assert client.get(f"/api/v1/documents/{did}/file", headers=hdr(client, up2)).status_code == 404
    f = client.get(f"/api/v1/documents/{did}/file", headers=hdr(client, up1))
    assert f.content == CSV and f.headers["cache-control"] == "private, no-store"
    assert client.get(f"/api/v1/documents/{did}/file").status_code == 401
    assert client.delete(f"/api/v1/documents/{did}", headers=hdr(client, rev)).status_code == 403


def test_upload_rejects_disguised_file(client, db):
    up = mkuser(db, UserRole.uploader)
    r = client.post("/api/v1/documents", headers=hdr(client, up), files={"file": ("x.pdf", b"MZ\x90\x00 not a pdf")})
    assert r.status_code == 422


def test_correction_audit_version_and_validation(client, db):
    rev = mkuser(db, UserRole.reviewer)
    doc = make_doc(db, rev, sha=uuid.uuid4().hex * 2, key=uuid.uuid4().hex)
    inv = make_invoice(db, doc, total=D("1200"),
                       line_items=[InvoiceLineItem(line_no=1, quantity=D("10"), unit_price=D("100"), total=D("1000"))])
    run_validation(db, inv.id)
    h = hdr(client, rev)
    cur = client.get(f"/api/v1/invoices/{inv.id}", headers=h).json()
    assert any(f["rule_id"] == "total_mismatch" for f in cur["findings"])
    assert client.post(f"/api/v1/invoices/{inv.id}/review", headers=h,
                       json={"version": cur["version"], "decision": "approve"}).status_code == 409  # blocked

    bad = client.put(f"/api/v1/invoices/{inv.id}", headers=h,
                     json={"version": cur["version"], "fields": {"total": 1150.0}})
    assert bad.status_code == 422  # floats refused
    r = client.put(f"/api/v1/invoices/{inv.id}", headers=h,
                   json={"version": cur["version"], "fields": {"total": "1150.00"}, "reason": "typo on scan"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == "1150.0000" and body["version"] > cur["version"]
    assert not any(f["rule_id"] == "total_mismatch" for f in body["findings"])  # re-validated
    assert client.put(f"/api/v1/invoices/{inv.id}", headers=h,
                      json={"version": cur["version"], "fields": {"total": "1"}}).status_code == 409  # stale
    audit = client.get(f"/api/v1/invoices/{inv.id}/corrections", headers=h).json()
    assert [(a["field"], a["old"], a["new"], a["reason"]) for a in audit] == [
        ("total", "1200.0000", "1150.00", "typo on scan")]
    ok = client.post(f"/api/v1/invoices/{inv.id}/review", headers=h,
                     json={"version": body["version"], "decision": "approve"})
    assert ok.status_code == 200 and ok.json()["review_status"] == "approved"
    # editing an approved invoice withdraws the approval
    again = client.put(f"/api/v1/invoices/{inv.id}", headers=h,
                       json={"version": ok.json()["version"], "fields": {"supplier_name": "ACME Limited"}})
    assert again.json()["review_status"] == "pending"
    assert db.query(ManualCorrection).filter_by(invoice_id=inv.id).count() == 2


def test_null_is_kept_distinct_from_zero_and_empty(client, db):
    rev = mkuser(db, UserRole.reviewer)
    doc = make_doc(db, rev, sha=uuid.uuid4().hex * 2, key=uuid.uuid4().hex)
    inv = make_invoice(db, doc)
    h = hdr(client, rev)
    v = client.get(f"/api/v1/invoices/{inv.id}", headers=h).json()["version"]
    r = client.put(f"/api/v1/invoices/{inv.id}", headers=h, json={"version": v, "fields": {"tax": None}})
    assert r.json()["tax"] is None
    assert client.put(f"/api/v1/invoices/{inv.id}", headers=h,
                      json={"version": r.json()["version"], "fields": {"supplier_name": ""}}).status_code == 422


def test_resolve_finding_requires_reason_and_unblocks_approval(client, db):
    rev = mkuser(db, UserRole.reviewer)
    doc = make_doc(db, rev, sha=uuid.uuid4().hex * 2, key=uuid.uuid4().hex)
    inv = make_invoice(db, doc, total=D("1200"))
    run_validation(db, inv.id)
    h = hdr(client, rev)
    fid = next(f["id"] for f in client.get(f"/api/v1/invoices/{inv.id}", headers=h).json()["findings"]
               if f["rule_id"] == "total_mismatch")
    assert client.post(f"/api/v1/findings/{fid}/resolve", headers=h, json={"status": "accepted", "note": ""}
                       ).status_code == 422
    assert client.post(f"/api/v1/findings/{fid}/resolve", headers=h,
                       json={"status": "accepted", "note": "supplier confirmed by phone"}).status_code == 200
    db.refresh(inv)
    ok = client.post(f"/api/v1/invoices/{inv.id}/review", headers=h,
                     json={"version": inv.version, "decision": "approve"})
    assert ok.status_code == 200, ok.text
    assert db.get(Invoice, inv.id).review_status == ReviewStatus.approved
    assert db.get(ValidationResult, uuid.UUID(fid)).resolution_status == ResolutionStatus.accepted


def test_reconciliation_endpoints_require_reviewer(client, db):
    up, rev = mkuser(db, UserRole.uploader), mkuser(db, UserRole.reviewer)
    a = make_doc(db, rev, sha=uuid.uuid4().hex * 2, key=uuid.uuid4().hex)
    b = make_doc(db, rev, sha=uuid.uuid4().hex * 2, key=uuid.uuid4().hex)
    make_invoice(db, a, invoice_number="Z-1", total=D("10"))
    make_invoice(db, b, invoice_number="Z-1", total=D("11"))
    body = {"left_document_ids": [str(a.id)], "right_document_ids": [str(b.id)]}
    assert client.post("/api/v1/reconciliation", headers=hdr(client, up), json=body).status_code == 403
    r = client.post("/api/v1/reconciliation", headers=hdr(client, rev), json=body)
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["matches"][0]["outcome"] == "discrepancies"
    csvr = client.get(f"/api/v1/reconciliation/{rep['run_id']}/csv", headers=hdr(client, rev))
    assert csvr.headers["content-type"].startswith("text/csv")
    rel = rep["matches"][0]["id"]
    assert client.post(f"/api/v1/relationships/{rel}/reject", headers=hdr(client, rev)).json()["status"] == "rejected"


def test_system_endpoints(client, db):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code in (200, 503)
    m = client.get("/metrics")
    assert m.status_code == 200 and "docint_outbox_unpublished" in m.text
    assert client.get("/health").headers["x-content-type-options"] == "nosniff"


def test_rate_limit_on_login(client, db, monkeypatch):
    from app.api import deps
    deps._limiter.cache_clear()
    from app.core.security import RateLimiter
    fresh = RateLimiter()
    monkeypatch.setattr(deps, "_limiter", lambda: fresh)
    u = mkuser(db, UserRole.viewer)
    codes = [client.post("/api/v1/auth/login", json={"email": u.email, "password": "bad"}).status_code
             for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_search_endpoint_auth_validation_and_scope(client, db):
    from app.api.routers.search import embedder_dep
    from app.search.embeddings import HashEmbedding
    client.app.dependency_overrides[embedder_dep] = lambda: HashEmbedding()
    assert client.get("/api/v1/search?q=x").status_code == 401
    u = mkuser(db, UserRole.uploader)
    h = hdr(client, u)
    assert client.get("/api/v1/search?q=", headers=h).status_code == 422
    assert client.get("/api/v1/search?q=" + "a" * 501, headers=h).status_code == 422
    assert client.get("/api/v1/search?q=abc&mode=bogus", headers=h).status_code == 422
    r = client.get("/api/v1/search?q=hello", headers=h)
    assert r.status_code == 200 and r.json()["results"] == []


def test_stats_pages_and_page_text_scoped(client, db):
    from app.models import DocumentPage, ExtractedText
    from app.models.base import PageKind
    a, b = mkuser(db, UserRole.uploader), mkuser(db, UserRole.uploader)
    doc = make_doc(db, a, sha=uuid.uuid4().hex * 2, key=uuid.uuid4().hex)
    db.add(DocumentPage(document_id=doc.id, page_number=1, kind=PageKind.digital, text_chars=5))
    db.add(ExtractedText(document_id=doc.id, page_number=1, text="hello", source="native"))
    db.flush()
    ha, hb = hdr(client, a), hdr(client, b)
    assert client.get("/api/v1/stats", headers=ha).json()["documents"] >= 1
    assert client.get("/api/v1/stats", headers=hb).json()["documents"] == 0
    assert client.get(f"/api/v1/documents/{doc.id}/pages", headers=ha).json()[0]["kind"] == "digital"
    assert client.get(f"/api/v1/documents/{doc.id}/pages/1/text", headers=ha).json()["text"] == "hello"
    assert client.get(f"/api/v1/documents/{doc.id}/pages/1/text", headers=hb).status_code == 404
    assert client.get(f"/api/v1/documents/{doc.id}/pages/9/text", headers=ha).status_code == 404
