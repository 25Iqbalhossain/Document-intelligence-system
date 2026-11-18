# Implementation Status

Last updated: Phase 1 complete.

## Completed phases
- Phase 1: Requirement analysis (docs).
- Phase 2: Repository skeleton, settings, docker-compose, Dockerfile, .env.example, README, minimal FastAPI app (`/health` only).
- Phase 3: SQLAlchemy models (14 tables), Alembic migration 0001, schema docs.
- Phase 5: LLM provider abstraction (OpenAI-compatible + deterministic mock), versioned prompt, Pydantic output contracts, evidence verification (quote must exist in the page text and contain the value), normalisation, field-level confidence (evidence x OCR x parse), repair retry + fallback model, page-window processing, document classifier, deterministic spreadsheet/CSV-to-invoice mapper with cell references, persistence (never overwrites reviewed work), pipeline chain extract_text -> ai_extract -> validate_financials -> processed | needs_review.
- Phase 6 (done ahead of 5): normalizers (Bengali/Arabic digits, lakh/crore and European number formats, day-first dates with ambiguity flags, currency, invoice/name keys), Decimal validation engine (9 rule modules covering the 10 required checks), persistence service, migration 0002.
- Phase 4: file validation, OCR abstraction, PDF/image/Excel/CSV processors; safe local storage; upload ingestion service; job/outbox orchestration (retry with exponential backoff, dead-letter, cancel, progress); Celery worker; page-batch persistence. No HTTP upload endpoint yet (Phase 8).

- Phase 7: reconciliation: field comparator (tolerance, format-vs-value differences), staged matcher (exact number+supplier auto-confirmed; number-only, fuzzy, attribute matches only *proposed*; duplicates and ties parked as ambiguous, never merged/deleted), `reconciliation_runs` + migration 0003, service (run, report, CSV with formula-injection guard, confirm/reject audited by user). Library/service only: no HTTP endpoint yet. Tested on synthetic records only; matching thresholds are untuned on real data.
- Phase 8: HTTP API under `/api/v1` (+ `/health`, `/ready`, `/metrics`): JWT login with rotating single-use refresh tokens and reuse detection, scrypt password hashing, admin/reviewer/uploader/viewer roles, owner scoping (foreign documents return 404), upload (magic-byte checked, rate limited), authenticated-only file download (no public URLs), progress/jobs/history/retry/cancel, invoice read, audited correction (`PUT /invoices/{id}`: version-checked, Decimal-as-string only, null kept distinct from empty/zero, re-validates, withdraws approval), finding resolve (reason required), review approve/reject (approval blocked by unresolved error findings), review queue, reconciliation run/report/CSV/confirm/reject. `scripts/create_admin.py` bootstraps the first admin. Verified once end-to-end by hand: HTTP upload -> Celery worker -> needs_review (CSV had no currency) -> invoices readable over HTTP.
- Phase 9: search. Embedding stage added to the chain (`validate_financials -> embed -> final status`; a document is only `processed`/`needs_review` after indexing; if indexing fails permanently the document goes to review with reason `search_indexing_failed` instead of being lost). Page-granular chunking (word-boundary overlap), idempotent per model. `GET /api/v1/search` = PostgreSQL full-text (`simple` config, no stemming, so non-English text works) + pgvector cosine, merged by reciprocal rank fusion with per-source ranks returned; invoice number/supplier lookup (number-format tolerant); ownership applied inside each query. **The default embedder is a hashed bag-of-words (`hash-bow-384`): it matches shared words, NOT meaning. It is labelled as such in the API and the UI. Real semantic search needs `DOCINT_EMBEDDING_PROVIDER=sentence_transformers` (not installed or tested here).** No retrieval-quality measurements exist.
- Phase 10: React + TypeScript + Vite + Tailwind + React Query frontend, 10 pages: login, dashboard, upload (multi-file, client-side checks), documents (filter/paginate), document detail (progress polling, pages and OCR text, jobs with retry/cancel, history, original file), invoice review (original beside editable fields, per-field confidence and source quote, audited corrections, finding resolution with mandatory reason, approve/reject), review queue, search, reconciliation (pick two sets, report, confirm/reject, CSV), users (admin). Access token in memory, rotating refresh token in sessionStorage. Files are fetched with auth as blobs. Role-aware navigation, but the server is the authority.
  Verified: `tsc` strict clean, `vite build`, 6 vitest unit tests, and a Playwright run in real Chromium against the live stack (uvicorn + Celery + Postgres + Redis): bad login, session restore on reload, upload rejection, CSV upload through the worker, edit+save+audit, resolve findings, approve, queue, search, reconciliation with discrepancy, user page, sign-out, and no horizontal overflow at 390px on 7 pages. That browser script is not committed as an automated suite yet.
  Also added: `frontend.Dockerfile` + `nginx.conf` (CSP, upload streaming, /metrics blocked), `beat` service and worker `-Q default,ocr` in compose. **None of the Docker files have been built or run (no Docker daemon).**

## Pending phases
8b (user disable/password change; per-IP limits behind a proxy), 11 Testing (commit the Playwright suite, frontend component tests), 12 Performance/Security, 13 Evaluation, 14 Deployment.

## Five-Day Roadmap

| Day | Goal | Output |
|-----|------|--------|
| 1 | Foundations and vertical slice start | Repo structure, Docker Compose (Postgres, Redis, API), SQLAlchemy models + Alembic migration, auth/RBAC, upload endpoint with validation and storage, job/outbox skeleton |
| 2 | Ingestion | PDF (digital + scanned, batching), image OCR pipeline, Excel and CSV processors, per-page records, worker tasks, processor tests |
| 3 | Intelligence | LLM abstraction + mock, extraction with schema validation and evidence check, normalizers (Bengali formats), validation rule engine with Decimal, duplicate detection, tests |
| 4 | Cross-document and search | Reconciliation (PDF-PDF, PDF-Excel, PDF-CSV), review/corrections/audit APIs, FTS + pgvector hybrid search, remaining API endpoints and tests |
| 5 | UI, hardening, delivery | React frontend (all pages), integration, security and performance checks, synthetic evaluation harness and measured report, Docker clean-install test, README, API docs, walkthrough and AI-assistance disclosure |

## Current architecture
See docs/architecture.md.

## Implemented modules
`app/core/*`, `app/models/*`, `app/processors/*`, `app/services/{storage,documents,orchestration,pipeline}.py`, `app/workers/{celery_app,tasks}.py`, `app/extraction/*` (normalizers, evidence, extractor, llm, schemas, classifier, tabular, prompts), `app/validation/*`, `app/services/extraction_service.py`, `app/services/validation_service.py`, `app/main.py` (health only), migrations 0001-0002.

## Known issues
- `docker compose up` still fails for the frontend: `infrastructure/docker/frontend.Dockerfile` does not exist. The worker module now exists but was only run outside Docker.
- A job left `pending` after a retry whose Celery message was lost has no recovery path (the janitor only re-queues `running` jobs). Add a pending-job sweeper.
- Beat (periodic outbox relay and stuck-job reset) is configured but not started in docker-compose; the worker relays child jobs itself, but root jobs rely on the API calling the relay or on beat.
- Text extraction ends in status `ai_extracting`; nothing consumes it yet.
- Compose, Dockerfile and requirements are untested (Docker was not run in this sandbox).
- Enum/status columns have no server defaults; raw SQL inserts must supply them.
- Normalizers do not parse Bengali month names (e.g. জানুয়ারি) or "Rs" (ambiguous between INR/PKR/LKR); such values return None and are flagged for review rather than guessed.
- Cross-document checks: same-number conflicts are caught by the duplicate rules; field-by-field comparison between two documents is Phase 7.
- **No real LLM has been called.** The OpenAI-compatible provider is tested against a mocked HTTP transport only, and the prompt has never been run on a real model. Extraction quality and the 0.85 fuzzy-evidence and 0.7 review thresholds are untuned guesses until Phase 13 measures them on labeled real documents.
- Page windows have no overlap: an invoice spanning a window boundary is split into two partial invoices (validation flags the totals; a human merges). Default window is 8 pages / 30k chars.
- The classifier is keyword-based (English + a few Bengali terms). A misclassified invoice is routed to review, not dropped, but non-invoice documents also go to review by default (`DOCINT_EXTRACTION_REVIEW_NON_INVOICES`).
- Spreadsheet mapping uses header synonyms only. Sheets without an invoice-number column and a total/subtotal column are skipped and listed in `documents.metadata.tabular_skipped`. Line items kept on a separate sheet are not joined to their header sheet.
- Final statuses (`processed` / `needs_review`) are currently set right after validation; the embedding stage (Phase 9) must be inserted before them.
- Reprocessing a document needs a `DOCINT_PIPELINE_VERSION` bump or a manual job retry (job idempotency keys include the pipeline version).
- Bengali OCR is untested (sandbox lacks the `ben` Tesseract language pack).
- OCR accuracy is only checked on clean/noisy synthetic text; no real documents were available.
- XLSX is loaded in normal (non-streaming) mode to read merged cells, so very large sheets use a lot of memory.
- Orientation detection (Tesseract OSD) is best-effort and returns the image unchanged when unsure.
- Dependencies in requirements.txt are not pinned; pin after the first clean install.

## Test results (measured)
306 backend tests pass (+ 6 frontend unit tests) (`pytest` in `backend/`): 13 schema, 36 processor, 24 text-pipeline, 65 normalizer, 30 validation-rule, 10 validation-service, 41 evidence/LLM-provider/schema, 25 extractor, 17 extraction-pipeline (DB). Set `DOCINT_REQUIRE_DB=1` so a missing database fails instead of skipping. Ruff clean.
Mutation check: disabling the evidence check fails 10 tests; disabling the re-extraction overwrite guard fails its test.
End-to-end (`scripts/e2e_worker_check.py`): real Celery worker (2 processes) + Redis + PostgreSQL + real Tesseract processed a 120-page digital PDF (7 jobs), a scanned PDF and a 3-sheet XLSX; after the extraction stages were added, all three ran the full chain (text -> classify/extract -> validate) and ended in `needs_review` (the mock LLM extracts nothing and never fabricates; the spreadsheet produced invoices that validation flagged for a missing currency), 100% pages done, OCR text contained the invoice number, 3.2 s total. Tiny synthetic files: this shows the plumbing works, not throughput.
Benchmarks: see docs/performance-notes.md.

## Exact next steps
1. Phase 11-12: security and performance checks, committed browser tests.
2. Obtain real labeled documents (and an LLM key) for Phase 13 evaluation; until then no accuracy figures can be reported.
