# Requirements: AI-Powered Document Intelligence System

Status: Phase 1 draft. Workspace was empty at start (greenfield).

## 1. Scope

Ingest business documents (digital PDF, scanned PDF, JPG/JPEG/PNG/TIFF, XLSX/XLS, CSV), extract invoice data, validate it deterministically, reconcile it across documents, make it searchable, and route uncertain results to human review.

Pipeline: Upload -> File validation -> Classification -> Text extraction -> OCR -> AI extraction -> Financial validation -> Storage -> Search -> Human review -> Final approval.

## 2. Functional Requirements

### FR-1 Ingestion
- FR-1.1 Single and bulk upload; per-file validation of extension, declared MIME, sniffed MIME (magic bytes), size, and content readability.
- FR-1.2 Detect digital vs scanned PDF per page (text-layer density heuristic), so mixed PDFs are handled correctly.
- FR-1.3 Process PDFs up to ~1,000 pages in page batches with bounded memory; page numbers preserved.
- FR-1.4 Images: orientation correction, denoise, deskew, threshold, OCR with confidence.
- FR-1.5 XLSX/XLS: all sheets, sheet names preserved, header detection, merged-cell handling.
- FR-1.6 CSV: encoding detection, delimiter sniffing, malformed-row capture, column names preserved.
- FR-1.7 Corrupted input is detected, recorded with a meaningful error, never crashes a worker, and is retryable. No page or sheet is skipped silently.

### FR-2 Classification
- FR-2.1 Classify each document (invoice, statement/ledger spreadsheet, other) before extraction; low-confidence classification routes to review.

### FR-3 AI Extraction
- FR-3.1 Extract: invoice_number, invoice_date, supplier_name, customer_name, subtotal, tax, total, currency, line_items (description, quantity, unit_price, tax, total).
- FR-3.2 Output validated against Pydantic schemas; malformed output triggers retry, then fallback, then review.
- FR-3.3 Missing values are `null`, never invented; `null` is distinct from `0`.
- FR-3.4 Per-field confidence, page/source references, and original extracted text preserved.
- FR-3.5 Model name/version and prompt version stored with every extraction.
- FR-3.6 LLM provider and embedding model are configurable behind abstractions.
- FR-3.7 Handle Bangladeshi and international date formats (DD/MM/YYYY, DD-MM-YYYY, ISO, month names) and currencies (BDT, Tk, ৳, USD, etc.), including lakh/crore digit grouping and Bengali numerals.

### FR-4 Financial Validation (deterministic, `Decimal` only)
Rules: required fields, valid date, duplicate invoice, subtotal = sum(line totals), line total = qty x unit price (+ tax where applicable), tax check, total = subtotal + tax, currency consistency, negative/invalid values, cross-document inconsistency.
Each result stores: rule id, severity, expected, actual, description, source references, resolution status. Rounding and tolerance are configurable.

### FR-5 Cross-Document Reconciliation
- PDF-to-PDF, PDF-to-Excel, PDF-to-CSV.
- Match on exact identifiers first, then tightly controlled fuzzy matching.
- Uncertain matches are never auto-merged; they require human confirmation.
- Detect conflicts, missing records, duplicates; produce discrepancy reports with traceable source references.

### FR-6 Search
- Keyword (PostgreSQL FTS), metadata filters (invoice number, supplier, date range, amount range), semantic (pgvector), and hybrid with relevance scoring. Results carry document and page references.

### FR-7 Review and Audit
- Editable extracted fields; every manual change recorded (who, when, old, new, reason).
- Review confirmation required to move to Approved. Duplicate detection never deletes records.

### FR-8 Jobs
- Async processing, status, retries with exponential backoff, idempotency, timeouts, cancellation where practical, dead-letter equivalent, document- and page-level progress, structured logs.

### FR-9 API
Endpoints as listed in the master prompt (documents, jobs, extraction, validation/review, search, reconciliation, health/ready/metrics) with pagination, filtering, sorting, consistent error schema, RBAC, rate limiting.

### FR-10 Frontend
Dashboard, Upload, Processing Monitor, Library, Document Details, Extraction Viewer, Validation & Review, Reconciliation Dashboard, Search, Processing History. Statistics come from real queries only.

## 3. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 Performance | 1,000-page PDF processed in page batches; peak worker memory bounded by batch size, not document size. Targets are goals until measured (see evaluation). |
| NFR-2 Reliability | Idempotent jobs; crash-safe resume at page-batch granularity; failed docs recoverable. |
| NFR-3 Security | Magic-byte verification, size limits, path-traversal-safe storage, non-public storage, authenticated and authorized downloads, RBAC, audit logs, no hardcoded secrets. |
| NFR-4 Observability | Structured JSON logs with correlation IDs, Prometheus metrics, health and readiness probes. |
| NFR-5 Maintainability | Typed Python, modular monolith, repository/service split, Ruff/mypy/ESLint clean. |
| NFR-6 Testability | LLM mocked in tests; real sample documents for integration/evaluation. |
| NFR-7 Honesty | No fabricated metrics. Evaluation labels results as measured / tested / estimated / untested. |
| NFR-8 Portability | Docker Compose clean install with documented commands. |

## 4. Assumptions (documented defaults)

1. Single-tenant deployment; users have roles `admin`, `reviewer`, `viewer`, `uploader`.
2. Local-filesystem storage behind a `StorageBackend` interface (S3-compatible adapter later).
3. Celery with Redis broker; Redis also used for rate limiting and caching.
4. Tesseract is the default OCR engine (English + Bengali language packs); PaddleOCR is an optional adapter.
5. LLM provider is selected by environment config; default adapter targets an OpenAI-compatible HTTP API; a deterministic mock provider ships for tests and keyless runs.
6. Embedding default is a local sentence-transformers model with a 384-dim vector; dimension is a config value fixed at migration time.
7. "Invoice" is the primary entity; spreadsheet rows representing invoices are normalized into the same `invoices` table with a `source_kind` field.
8. Max upload size default 100 MB per file (configurable); 1,000-page PDFs above that limit require raising the config.
9. Default currency when absent is `null`, never assumed. Rounding default: 2 decimal places, ROUND_HALF_UP, tolerance 0.01.
10. Real labeled sample documents are not yet provided; evaluation will use synthetic generated invoices clearly labeled as synthetic until real data is supplied.

## 5. Ambiguities Needing Eventual Confirmation

- Exact definition of "Final Approval" authority (assumed: reviewer or admin role).
- Whether tax is per-line, header-level, or both for the user's real documents (supported: both).
- Retention policy for soft-deleted documents (assumed: retained until admin purge).
- Real document corpus availability for evaluation.
- LLM provider and data-residency constraints (financial data leaving the network).
