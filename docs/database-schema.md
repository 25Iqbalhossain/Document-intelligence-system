# Database Schema

Source of truth: SQLAlchemy models in `backend/app/models/` and migration `backend/migrations/versions/0001_initial_schema.py`.
The ER diagram is in `docs/architecture.md` section 5.

Verified on PostgreSQL 16 with pgvector: 14 tables. Migrations 0001 (initial) and 0002 (additive: `validation_results.field_name`, expression index `ix_invoices_number_key`) upgrade, downgrade and re-upgrade cleanly; `alembic check` reports no drift.

## Conventions
- UUID primary keys (`gen_random_uuid()` server default; `uuid4` in the ORM).
- `created_at` / `updated_at` (timestamptz) on every table.
- Soft delete (`deleted_at`) on `documents` and `invoices`.
- Money is `NUMERIC(18,4)`; quantity is `NUMERIC(18,6)`. `NULL` means "not found in source", never zero.
- Enums are stored as VARCHAR with CHECK constraints (`ck_<table>_<enumclass>`), so adding a value is an additive migration.
- JSONB for flexible metadata, confidence maps, source references and the untouched raw LLM output.

## Tables
| Table | Purpose | Notable constraints / indexes |
|-------|---------|-------------------------------|
| users | Accounts and roles | unique email |
| refresh_tokens | Hashed refresh tokens, revocation | unique token_hash |
| documents | Uploaded files | unique storage_key; partial index on sha256 (live rows); GIN on metadata |
| document_pages | One row per page / image / sheet / CSV, with per-page status and error | unique (document_id, page_number) |
| processing_jobs | Stage and page-batch jobs | unique idempotency_key; (status, stage) index; parent_job_id for batches |
| outbox | Transactional outbox for broker delivery | partial index on unpublished rows |
| extracted_text | Per-page text, native or OCR | generated `tsv` column with GIN index; unique (document, page, source) |
| processing_history | Append-only event log | |
| invoices | Extracted invoice header | composite lookup index plus an expression index on the canonical invoice number (`regexp_replace(upper(invoice_number), '[^A-Z0-9]', '', 'g')`) for duplicate lookups (non-unique); `version` column for optimistic locking |
| invoice_line_items | Line items | unique (invoice_id, line_no) |
| validation_results | Rule outcomes, with `field_name` identifying the field the finding concerns | open-issue index on (resolution_status, severity) |
| document_relationships | Duplicate and reconciliation links, status proposed/confirmed/rejected | |
| document_embeddings | Chunk embeddings, `vector(384)` | HNSW index, cosine ops; unique per chunk and model |
| manual_corrections | Append-only audit of human edits | |

## Design notes
- Duplicates are allowed at the database level by design (business rule: never auto-delete). They are detected by a validation rule and linked via `document_relationships`.
- `ocr_confidence` (on pages and text) and `invoices.confidence` (per-field extraction confidence) are separate, as required.
- Embedding dimension is fixed at 384 in the migration; changing models with a different dimension needs a new migration and re-embedding.
- Python-side defaults cover ORM inserts. Raw SQL inserts must supply `doc_type` and `status`, because those columns have no server default yet.

## Tested behaviors (`backend/tests/integration/test_schema.py`, 13 tests)
NULL vs zero money, Decimal round-trip, duplicates allowed, unique idempotency key, unique page per document, CHECK rejects invalid enum, optimistic locking raises on stale update, cascade delete, line-number uniqueness, generated full-text column, pgvector cosine ranking, validation and audit row defaults.
