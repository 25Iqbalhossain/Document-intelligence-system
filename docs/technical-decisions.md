# Technical Decisions

Format: decision, rationale, trade-off.

## TD-1 Modular monolith + Celery workers
Rationale: one deployable and one schema keep transactions simple; workers scale on the expensive stages (OCR, LLM). Trade-off: module boundaries are enforced by convention and import-linting, not the network.

## TD-2 Celery over lighter queues (RQ, Arq)
Rationale: mature retry/backoff, time limits, routing to separate queues, and canvas primitives. Trade-off: heavier config. Redis is the broker; results are stored in PostgreSQL job rows, not the Celery result backend, so job state survives Redis loss.

## TD-3 Transactional outbox
Rationale: inserting a job and publishing to Redis are two systems. The outbox row commits with the job; a relay publishes and marks `published_at`. Trade-off: slight latency, one extra table and relay loop. Idempotency keys make double-publish harmless.

## TD-4 Page-batch sub-jobs for PDFs
Rationale: bounds memory, gives page-level progress, limits crash blast radius, enables partial resume. Trade-off: more rows; 1,000 pages at 20/batch is 50 sub-jobs, which is acceptable.

## TD-5 Per-page digital/scanned detection
Rationale: real PDFs are mixed. A page is treated as scanned if native text is below a character threshold or covered mostly by an image. Trade-off: heuristic; thresholds are configurable and evaluated on sample data.

## TD-6 Tesseract default, PaddleOCR optional adapter
Rationale: Tesseract is easy to containerize and supports Bengali (`ben`) language data. PaddleOCR can be more accurate but adds heavy dependencies. Both sit behind an `OcrEngine` interface.

## TD-7 LLM extracts, code decides
Rationale: LLMs are unreliable at arithmetic and may hallucinate. Extraction returns values plus evidence quotes; deterministic code normalizes, validates, and checks that evidence exists in the source text. Trade-off: extra pipeline stage; far fewer silent errors.

## TD-8 Money as NUMERIC(18,4) and Python Decimal
Rationale: no float error. Four decimal places allow unit prices with sub-cent precision; display rounds per config.

## TD-9 Duplicates flagged, not constrained
Rationale: business rule says duplicates must not be auto-deleted. A DB unique constraint would reject legitimate re-uploads and hide the conflict. Detection is a validation rule plus a `duplicate_of` relationship. Exact file re-upload (same SHA-256) is surfaced to the user with a link to the existing document.

## TD-10 pgvector with HNSW, dimension fixed per migration
Rationale: no separate vector DB; transactional consistency with metadata. Trade-off: changing embedding model dimension requires a migration and re-embedding; `model_name` is stored per row so mixed models are detectable.

## TD-11 Hybrid search by reciprocal rank fusion
Rationale: avoids tuning score scales between ts_rank and cosine similarity. Trade-off: rank-based, so absolute relevance is not comparable across queries; raw component scores are returned too.

## TD-12 JWT auth with RBAC
Rationale: stateless API, simple frontend integration. Refresh tokens stored hashed with revocation. Trade-off: token revocation requires the refresh table.

## TD-13 Local storage behind a StorageBackend interface
Rationale: simple Docker deployment; S3 adapter can follow without touching services.

## TD-14 Synthetic evaluation data until real data supplied
Rationale: no labeled corpus exists in the workspace. A generator will produce invoices with known ground truth in several layouts (including Bangladeshi formats and scanned-style degradations). Reports will label results as synthetic. Real-document results can only be reported once real labeled documents are provided.

## TD-15 Mock LLM provider
Rationale: the system must run and test without API keys. The mock returns deterministic fixtures keyed by input hash. It is never used to produce reported accuracy numbers.
