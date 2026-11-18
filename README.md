# AI-Powered Document Intelligence System

Status: **in development, not finished.** Built: ingestion, job pipeline, evidence-verified AI extraction, deterministic validation, reconciliation, HTTP API, hybrid search, React frontend. Not done: security/performance verification, accuracy evaluation (needs real labelled documents and an LLM key, so **no accuracy figures exist**), and a verified Docker install. See `docs/IMPLEMENTATION_STATUS.md` for exact status and known gaps.

## Run locally (development)
Requires Python 3.12+, Node 20+, PostgreSQL 16 with pgvector, Redis, Tesseract.
```bash
cp .env.example .env     # set DOCINT_SECRET_KEY (>= 32 chars) and database settings; export them or use a .env loader
cd backend && pip install -r requirements.txt && alembic upgrade head
python ../scripts/create_admin.py you@example.com      # password via DOCINT_ADMIN_PASSWORD or prompt
uvicorn app.main:app --port 8000 &
celery -A app.workers.celery_app worker -Q default,ocr -c 2 &
celery -A app.workers.celery_app beat &                 # outbox relay + stuck-job recovery
cd ../frontend && npm ci && npm run dev                 # http://localhost:5173 (proxies /api to :8000)
```
Tests: `cd backend && DOCINT_REQUIRE_DB=1 pytest` (needs Postgres) and `cd frontend && npm test`.
Without an LLM key (`DOCINT_LLM_PROVIDER=mock`) PDFs/images are OCR'd but invoices are only extracted from spreadsheets/CSV; set `openai_compatible` plus base URL, key and model for AI extraction.

`docker compose up --build` is provided (`postgres`, `redis`, `migrate`, `api`, `worker`, `beat`, `frontend` on :8080) but has **not been built or run** in the environment this was written in.

## Docs
`docs/requirements.md`, `docs/architecture.md`, `docs/technical-decisions.md`, `docs/risk-analysis.md`, `docs/database-schema.md`.
