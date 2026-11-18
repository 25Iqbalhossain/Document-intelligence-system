# Risk Analysis

Likelihood (L) and impact (I): H/M/L.

| # | Risk | L | I | Mitigation |
|---|------|---|---|-----------|
| R1 | LLM hallucinates or misreads financial values | H | H | Evidence-quote check against source text; deterministic validation; null-over-guess prompt; low-confidence routes to review |
| R2 | OCR errors on scanned/low-quality documents (digits confused, e.g. 5/6, 0/O) | H | H | Preprocessing; per-page OCR confidence stored separately; arithmetic validation catches many digit errors; review queue |
| R3 | 1,000-page PDF exhausts memory or runs very long | M | H | Batch processing; render only needed pages; worker memory limits and recycling (`max-tasks-per-child`); timeouts; benchmark |
| R4 | Redis outage or worker crash loses jobs | M | H | Outbox pattern; job state in PostgreSQL; idempotent stages; resume per batch |
| R5 | Malicious or malformed uploads (zip bombs, crafted PDFs, macro Excel) | M | H | Magic-byte checks, size and page caps, reject macros, parse in resource-limited workers, no execution of content |
| R6 | Wrong reconciliation merges | M | H | Exact first; fuzzy only proposes; human confirmation required; all matches traceable |
| R7 | Bengali/mixed-language text and number formats mis-parsed | M | M | Normalizers for Bengali digits, lakh/crore grouping, DD/MM ambiguity handling; ambiguous dates flagged, not guessed |
| R8 | Date ambiguity (03/04/2025) | H | M | Locale-ordered parsing with configured default (day-first); ambiguous cases flagged for review |
| R9 | Sensitive financial data sent to external LLM | M | H | Provider is configurable; self-hosted/OpenAI-compatible endpoints supported; redaction hook; documented in deployment guide |
| R10 | No labeled real data, so accuracy cannot be measured honestly | H | H | Synthetic generator with ground truth, clearly labeled; evaluation harness accepts real labeled data; report separates measured vs untested |
| R11 | Embedding model change invalidates vectors | L | M | Model name stored per row; re-embed job; dimension in config and migration |
| R12 | Scope too large for the timeline | H | M | Vertical slice first (PDF digital -> extraction -> validation -> UI), then breadth; status tracked in IMPLEMENTATION_STATUS.md |
| R13 | Tesseract and system dependencies complicate Docker | M | M | Pin in Dockerfile; language packs installed explicitly; CI-style smoke test |
| R14 | Schema churn after migrations | M | M | Finalize schema in Phase 3 before services; additive migrations afterwards |
| R15 | Duplicate false positives/negatives | M | M | Normalized composite key plus amount/date similarity; flagged with severity, never deleted |
| R16 | Concurrent edits to the same invoice | L | M | Optimistic locking via `version` column on invoices; corrections reject stale versions |
| R17 | Prompt injection through document text | M | M | Document text is delimited as data; output is schema-validated; LLM has no tools; results never executed |

## Highest-priority risks

R1, R2, R3, R6, R10 drive the design: they are why validation is deterministic, why every field has evidence and confidence, why PDFs are batched, why reconciliation needs human confirmation, and why evaluation separates measured from untested results.
