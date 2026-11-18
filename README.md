# AI-Powered Document Intelligence & Financial Reconciliation System

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-3178C6.svg)](https://www.typescriptlang.org/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-336791.svg)](https://github.com/pgvector/pgvector)
[![Celery](https://img.shields.io/badge/Celery-5.4-37814A.svg)](https://docs.celeryq.dev/)

> **Architectural Philosophy:** *"LLM extracts, code decides."*  
> Language models are probabilistic pattern extractors, not financial calculators or compliance authorities. This system pairs LLM-driven structured schema extraction with deterministic Python `Decimal` financial validation, exact and normalized evidence verification (anti-hallucination guardrails), multi-tier reconciliation, and a human-in-the-loop review workflow.

---

## Table of Contents

1. [Executive Overview](#executive-overview)
2. [System Architecture](#system-architecture)
   - [High-Level Architecture](#high-level-architecture)
   - [Document Processing State Machine](#document-processing-state-machine)
   - [Core Engineering Patterns](#core-engineering-patterns)
3. [Core Subsystems](#core-subsystems)
   - [1. Ingestion & Preprocessing](#1-ingestion--preprocessing)
   - [2. AI Extraction & Evidence Verification](#2-ai-extraction--evidence-verification)
   - [3. Deterministic Financial Validation Engine](#3-deterministic-financial-validation-engine)
   - [4. Cross-Document Reconciliation](#4-cross-document-reconciliation)
   - [5. Hybrid Search (FTS + pgvector)](#5-hybrid-search-fts--pgvector)
   - [6. Human-in-the-Loop Review & Audit Trail](#6-human-in-the-loop-review--audit-trail)
   - [7. Modern Web Interface](#7-modern-web-interface)
4. [Repository Layout](#repository-layout)
5. [Prerequisites & System Requirements](#prerequisites--system-requirements)
6. [Local Environment Setup (Step-by-Step)](#local-environment-setup-step-by-step)
   - [Option A: Native Local Development (Recommended for Active Dev)](#option-a-native-local-development-recommended-for-active-dev)
   - [Option B: Containerized Deployment via Docker Compose](#option-b-containerized-deployment-via-docker-compose)
7. [Environment Configuration Reference (.env)](#environment-configuration-reference-env)
8. [API Architecture & Key Endpoints](#api-architecture--key-endpoints)
9. [Code Quality & Runtime Verification](#code-quality--runtime-verification)
10. [Troubleshooting & Senior Developer Field Notes](#troubleshooting--senior-developer-field-notes)
11. [Project Status & Roadmap](#project-status--roadmap)

---

## Executive Overview

The **AI-Powered Document Intelligence & Financial Reconciliation System** is an enterprise-grade document processing platform built to ingest, OCR, classify, extract, validate, reconcile, index, and audit business documents (invoices, receipts, financial statements, and spreadsheets).

### Key Capabilities
- **Multi-Format Ingestion:** Digital PDFs, scanned PDFs, images (PNG, JPEG, TIFF), spreadsheets (XLSX, XLS), and CSV files.
- **Dynamic Digital vs. Scanned Heuristics:** Evaluates text-layer density per page to dynamically switch between native text parsing and computer-vision OCR.
- **Anti-Hallucination Evidence Verification:** Requires the LLM to return exact quote spans alongside extracted fields. Extracted values must be verifiable against the source text or confidence is penalized and sent to human review.
- **Zero-Tolerance Financial Math:** 100% of arithmetic (subtotals, line items, taxes, totals, cross-line multiplication) is performed using Python `Decimal`—never floating-point numbers.
- **Cross-Document Reconciliation:** Matches documents (e.g., PDF invoice vs. Excel vendor statement vs. bank CSV) using exact identifier matching cascading down to controlled fuzzy matching.
- **Hybrid Search Engine:** Combines PostgreSQL full-text search (`tsvector` via `websearch_to_tsquery`) with pgvector semantic vector search merged via Reciprocal Rank Fusion (RRF).
- **Strict Role-Based Access Control (RBAC):** Token-based authentication with single-use rotating refresh tokens and reuse detection. Roles include `admin`, `reviewer`, `uploader`, and `viewer`.
- **Complete Audit Trail:** Every manual correction and lifecycle state transition is recorded in append-only audit logs.

---

## System Architecture

The system is designed as a **modular monolith** with decoupled, asynchronous background workers. This maintains transactional integrity and simplicity while allowing compute-heavy OCR and LLM operations to scale horizontally.

### High-Level Architecture

```mermaid
flowchart TD
    subgraph ClientLayer["Presentation Layer"]
        UI["React 18 + Vite SPA<br/>(Port 5173)"]
    end

    subgraph APILayer["API Gateway & Domain Services"]
        API["FastAPI Modular Monolith<br/>(Port 8000)"]
        UI -->|"/api/* (Vite Proxy)"| API
        AUTH["Auth & RBAC<br/>(JWT + Rotating Refresh)"]
        INGEST["Ingestion Service<br/>(Magic-byte validation)"]
        VAL["Validation Engine<br/>(Decimal Rule Engine)"]
        REC["Reconciliation Service"]
        SEARCH["Hybrid Search Service<br/>(RRF Engine)"]
        API --> AUTH
        API --> INGEST
        API --> VAL
        API --> REC
        API --> SEARCH
    end

    subgraph StorageLayer["Data & Persistence"]
        PG[("PostgreSQL 16 + pgvector<br/>(Relational, FTS, Vectors)")]
        STORAGE[("Private File Storage<br/>(/data/storage)")]
        API --> PG
        API --> STORAGE
    end

    subgraph QueueLayer["Async Orchestration & Reliability"]
        OUTBOX["Transactional Outbox<br/>(PostgreSQL)"]
        REDIS[("Redis 7<br/>(Broker & Rate Limiter)")]
        BEAT["Celery Beat<br/>(Outbox Relay & Janitor)"]
        API -->|Atomic Commit| OUTBOX
        BEAT -->|Relay Events| REDIS
        OUTBOX -.->|Polled by Beat| BEAT
    end

    subgraph WorkerLayer["Asynchronous Celery Workers"]
        W_OCR["Worker (OCR Queue)<br/>PyMuPDF + Tesseract"]
        W_DEF["Worker (Default Queue)<br/>AI Extract, Validation, Embedding"]
        REDIS -->|ocr queue| W_OCR
        REDIS -->|default queue| W_DEF
        W_OCR --> PG
        W_OCR --> STORAGE
        W_DEF --> PG
        W_DEF --> STORAGE
        W_DEF -->|Inference Call| LLM["LLM Provider (OpenAI / vLLM / Mock)"]
        W_DEF -->|Embeddings| EMB["Embedding Provider (pgvector / Sentence-Transformers)"]
    end
```

### Document Processing State Machine

Documents progress through a deterministic, idempotent state machine:

```mermaid
stateDiagram-v2
    [*] --> uploaded: Ingested & SHA-256 Verified
    uploaded --> validating: Magic bytes & size checks
    validating --> classified: Determine doc type
    classified --> extracting_text: Spawn page-batch sub-jobs
    extracting_text --> ai_extracting: Assemble text & page windows
    ai_extracting --> validating_financials: Structured schema parsed
    validating_financials --> embedding: Decimal validation executed
    embedding --> processed: All validation passed & indexed
    embedding --> needs_review: Low confidence / validation error / non-invoice
    needs_review --> approved: Human Reviewer approves
    needs_review --> needs_review: Manual corrections applied
    needs_review --> rejected: Human Reviewer rejects
    
    validating --> failed: Corrupted / invalid format
    extracting_text --> failed: OCR unrecoverable
    ai_extracting --> failed: Max retry attempts exceeded
    
    failed --> [*]
    approved --> [*]
    rejected --> [*]
```

### Core Engineering Patterns

1. **Transactional Outbox Pattern:** Creating a document job and enqueuing a Celery message span two distinct storage systems. To prevent distributed race conditions or lost jobs during broker outages, the API transaction commits the job row and an `outbox` event atomically to PostgreSQL. The Celery Beat relay reads unpublished outbox rows and delivers them to Redis with acknowledgment.
2. **Page-Batch Sub-Jobs (Constant Memory Footprint):** Documents up to ~1,000 pages are split into discrete sub-jobs (default: 20 pages per batch). PyMuPDF processes and releases pixmaps batch by batch. A worker crash aborts at most one batch, and progress (`pages_done / pages_total`) is updated in real time.
3. **Idempotent Job Pipeline:** Every job execution key is generated as:
   ```text
   idempotency_key = SHA-256(document_id + stage + input_hash + pipeline_version)
   ```
   Re-running an already completed stage is an immediate no-op.
4. **Optimistic Locking for Corrections:** Invoice reviews and edits use an incrementing `version` column. Concurrent edits from multiple reviewers result in an immediate `409 Conflict` rather than silent overwrites.
5. **Circuit Breaking & Fallback LLMs:** If the primary LLM fails schema extraction after an automated JSON repair prompt, the extractor cascades to a configured fallback model before gracefully routing the document to human review.

---

## Core Subsystems

### 1. Ingestion & Preprocessing
*Location: `backend/app/processors/`*
- **File Validation (`file_checks.py`):** Multi-tier validation against file extensions, declared MIME types, and sniffed magic bytes. Blocks zero-byte files, nested archive bombs, macro-enabled spreadsheets (e.g. `.xlsm`), and encrypted PDFs.
- **PDF Processor (`processors/pdf/`):** Evaluates character density. If a page has native extractable text above threshold, it extracts directly via PyMuPDF. If scanned or image-heavy, it renders the page at 300 DPI and invokes OCR.
- **Image Preprocessing (`processors/ocr.py`):** Deskewing, grayscale conversion, and median-filter noise reduction prior to Tesseract OCR.
- **Spreadsheets & CSV (`processors/excel/`, `processors/csv/`):** Reads all sheets with merged-cell expansion (openpyxl/xlrd) and sniffed delimiters/encodings (charset-normalizer).

### 2. AI Extraction & Evidence Verification
*Location: `backend/app/extraction/`*
- **Pydantic Contracts (`schemas.py`):** Invoices require strict typing: `invoice_number`, `invoice_date`, `supplier_name`, `customer_name`, `subtotal`, `tax`, `total`, `currency`, and `line_items`. Missing values become `null` (never assumed as zero).
- **Anti-Hallucination Verifier (`evidence.py`):** For each non-null extracted value, the LLM must provide a verbatim `evidence` quote from the document. The verifier checks that:
  1. The quote exists in the source text (via normalized substring search or Levenshtein distance).
  2. The extracted value is contained within the quoted evidence span.
  Extractions failing evidence verification have their confidence downgraded and trigger human review.
- **Regional & International Normalizers (`normalizers.py`):**
  - Bengali & Arabic numeral conversion (e.g., `০১২৩৪৫৬৭৮৯` $\rightarrow$ `0123456789`).
  - South Asian number formatting (lakh/crore: `1,00,000.00`) and European decimal commas (`1.000,00`).
  - Date normalization handling `DD/MM/YYYY`, `MM/DD/YYYY`, and ISO formats, raising ambiguity warnings when day and month are interchangeable ($\le 12$).
  - Multi-currency detection (`BDT`, `৳`, `USD`, `\$`, `EUR`, `€`, `GBP`, `£`, `INR`, `₹`).

### 3. Deterministic Financial Validation Engine
*Location: `backend/app/validation/`*
The validation engine is completely separated from LLMs and operates purely on Python `Decimal` arithmetic. It registers 9 distinct validation rule suites:
1. `required_fields`: Asserts presence of mandatory identifiers and monetary values.
2. `invoice_date_valid`: Flags future dates or dates outside the plausible business boundary.
3. `duplicate_invoice`: Identifies duplicates and conflicting data across existing records for the same supplier without destructive deletion.
4. `subtotal_matches_lines`: Verifies $\sum (\text{line totals}) = \text{subtotal}$ within configurable rounding tolerances.
5. `line_totals_match`: Verifies $\text{quantity} \times \text{unit price} (+ \text{tax}) = \text{line total}$.
6. `tax_matches_lines`: Asserts header tax equals line tax sum and flags if tax exceeds subtotal.
7. `total_matches`: Verifies $\text{subtotal} + \text{tax} = \text{total}$.
8. `currency_consistent`: Detects conflicting or mixed currencies across header and line items.
9. `numbers_valid`: Checks for non-finite values, negative amounts (credit notes), zero quantities, or implausibly large totals.

### 4. Cross-Document Reconciliation
*Location: `backend/app/reconciliation/`, `backend/app/services/reconciliation_service.py`*
- Compares sets of documents across formats (PDF invoice vs. PDF invoice, PDF vs. Excel ledger, PDF vs. bank CSV).
- **Staged Matching Pipeline:**
  1. *Exact Match:* Normalized invoice number + normalized supplier name (eligible for automated confirmation).
  2. *Invoice Number Match:* Matches invoice number with supplier discrepancy.
  3. *Fuzzy Match:* Token-based similarity, date proximity, and amount matching.
- **Discrepancy Reporting:** Generates itemized diffs. Exportable to CSV with formula injection protection (`=`/`+`/`-`/`@` prefixes sanitized).

### 5. Hybrid Search (FTS + pgvector)
*Location: `backend/app/search/`, `backend/app/services/search_service.py`*
- Combines lexical and semantic search:
  - **Full-Text Search:** Uses PostgreSQL `websearch_to_tsquery` over `extracted_text.tsv` (Simple dictionary, enabling multilingual support).
  - **Semantic Vector Search:** pgvector cosine distance over 384-dimensional document chunk embeddings with an HNSW index.
- **Reciprocal Rank Fusion (RRF):** Merges rank positions from keyword and vector candidate sets:
  $$\text{RRF}(d) = \sum_{m \in \{\text{fts}, \text{vector}\}} \frac{1}{60 + \text{rank}_m(d)}$$

### 6. Human-in-the-Loop Review & Audit Trail
*Location: `backend/app/services/corrections.py`, `backend/app/models/invoices.py`*
- Pending review queue (`GET /api/v1/review/queue`) filters invoices flagged by low confidence or validation errors.
- Side-by-side verification: Original source document viewer alongside editable structured form.
- Mandated audit reasons: Resolving validation findings (`accepted` or `dismissed`) requires a minimum 3-character audit justification.
- Corrections log: Changes are appended to `manual_corrections` (`field_name`, `old_value`, `new_value`, `reason`, `user_id`, `created_at`).

### 7. Modern Web Interface
*Location: `frontend/`*
- Single-page application built with **React 18**, **TypeScript**, **Tailwind CSS**, and **TanStack React Query**.
- 10 Dedicated views: Dashboard, Upload, Processing Monitor, Document Library, Document Details, Invoice Review Studio, Review Queue, Cross-Document Reconciliation, Hybrid Search, and User Management.

---

## Repository Layout

```plaintext
document-intelligence-system/
├── backend/                        # Backend FastAPI Application & Celery Workers
│   ├── alembic.ini                 # Alembic database migration configuration
│   ├── pyproject.toml              # Ruff and Pytest settings
│   ├── requirements.txt            # Pinned Python production dependencies
│   ├── app/
│   │   ├── main.py                 # FastAPI application factory & middleware setup
│   │   ├── api/                    # HTTP layer
│   │   │   ├── deps.py             # Dependency injection (Auth, DB, RBAC, Rate limiter)
│   │   │   └── routers/            # Routers: auth, documents, invoices, reconciliation, search, system
│   │   ├── core/                   # Infrastructure settings, security primitives, DB session
│   │   ├── extraction/             # AI extraction, prompts, normalizers, evidence verifier
│   │   ├── models/                 # SQLAlchemy 2.0 ORM declarative models (14 tables)
│   │   ├── processors/             # Format processors: PDF, Images, Excel, CSV, OCR
│   │   ├── reconciliation/         # Matching strategies, fuzzy scoring, discrepancy logic
│   │   ├── repositories/           # Encapsulated SQLAlchemy database queries
│   │   ├── schemas/                # Shared Pydantic data transfer schemas
│   │   ├── search/                 # Text search & pgvector embedding abstractions
│   │   ├── services/               # Core business services (orchestration, validation, review)
│   │   ├── utils/                  # Logging, text parsing, formatting utilities
│   │   ├── validation/             # Deterministic Decimal financial validation engine
│   │   └── workers/                # Celery app configuration, queue routing, and tasks
│   └── migrations/                 # Alembic migration scripts
├── frontend/                       # React 18 + TypeScript + Vite Single Page Application
│   ├── package.json                # Frontend dependencies & npm scripts
│   ├── vite.config.ts              # Vite configuration (proxying /api to port 8000)
│   ├── tailwind.config.js          # Tailwind CSS styling framework
│   └── src/
│       ├── api/                    # Typed API client & contracts
│       ├── auth/                   # Authentication context & token management
│       ├── components/             # Reusable UI components & document viewers
│       ├── layouts/                # Application navigation layouts
│       ├── pages/                  # Page views: Dashboard, Review, Search, Reconciliation, etc.
│       └── utils/                  # Client-side formatting helpers
├── infrastructure/                 # Container deployment configurations
│   └── docker/
│       ├── backend.Dockerfile      # Multi-stage Python 3.12 + Tesseract container
│       └── frontend.Dockerfile     # Multi-stage Node.js build container
├── scripts/                        # Administrative operations
│   └── create_admin.py             # CLI script to bootstrap initial administrator account
├── docs/                           # Architecture specs, requirements, and technical logs
├── .env.example                    # Template environment variables
├── docker-compose.yml              # Multi-container orchestration definition
└── README.md                       # Master system documentation
```

---

## Prerequisites & System Requirements

Ensure the following runtimes and services are available on your host system:

| Dependency | Minimum Version | Notes |
|------------|-----------------|-------|
| **Python** | `3.12+` | Required for backend typing and performance improvements |
| **Node.js** | `20+` (or `22+`) | Required for building and running the Vite frontend |
| **PostgreSQL** | `16+` with `pgvector` | Requires the `pgvector` extension installed (`pgvector/pgvector:pg16`) |
| **Redis** | `7.0+` | Used as Celery message broker and rate limiting backend |
| **Tesseract OCR** | `5.3+` | Needs `eng` (English) and optionally `ben` (Bengali) language packs |

### Platform-Specific OS Packages

- **Ubuntu / Debian:**
  ```bash
  sudo apt-get update
  sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-ben libgl1 libglib2.0-0
  ```
- **macOS (Homebrew):**
  ```bash
  brew install tesseract tesseract-lang redis
  ```
- **Windows:**
  - Install Tesseract via the official [UB-Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki).
  - Add `C:\Program Files\Tesseract-OCR` to your system `PATH`.
  - For Redis and PostgreSQL on Windows, running them via lightweight Docker containers is strongly recommended (see below).

---

## Local Environment Setup (Step-by-Step)

### Option A: Native Local Development (Recommended for Active Dev)

Follow this guide to run the services directly on your host machine.

#### Step 1: Clone and Configure Environment

```bash
# 1. Clone the repository
git clone <repo-url> document-intelligence-system
cd document-intelligence-system

# 2. Copy the environment configuration template
cp .env.example .env
```

Generate a secure 48-byte secret key and paste it into `.env`:
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env` to verify your database credentials and secret key:
```ini
DOCINT_ENVIRONMENT=development
DOCINT_DATABASE_URL=postgresql+psycopg://docint:change-me@localhost:5432/docint
DOCINT_REDIS_URL=redis://localhost:6379/0
DOCINT_SECRET_KEY=<paste_generated_key_here>
DOCINT_STORAGE_ROOT=./storage_data
DOCINT_LLM_PROVIDER=mock
DOCINT_EMBEDDING_PROVIDER=mock
```

> **Tip for Windows/Mac Developers:** If you do not have native Postgres/Redis installed, spin them up with Docker while running your Python and React code locally:
> ```bash
> docker run -d --name docint-postgres -p 5432:5432 -e POSTGRES_USER=docint -e POSTGRES_PASSWORD=change-me -e POSTGRES_DB=docint pgvector/pgvector:pg16
> docker run -d --name docint-redis -p 6379:6379 redis:7-alpine
> ```

#### Step 2: Backend Virtual Environment & Dependencies

```bash
# Navigate to backend
cd backend

# Create and activate Python virtual environment
# Linux/macOS:
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell):
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install backend dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

#### Step 3: Run Database Migrations

Apply Alembic migrations to set up the 14 relational tables, vector dimensions, and indexes:
```bash
alembic upgrade head
```

#### Step 4: Bootstrap the Initial Admin User

Create your primary system administrator account using the management script:
```bash
# You can set the password via environment variable or enter it interactively (min 12 chars)
DOCINT_ADMIN_PASSWORD="YourSecurePassword123!" python ../scripts/create_admin.py admin@example.com
```

#### Step 5: Start the Backend Services

To run the full backend orchestration pipeline, you will open 3 separate terminal sessions:

##### Terminal 1: FastAPI API Server
```bash
cd backend
# With .venv activated:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
*API is accessible at `http://localhost:8000` (Interactive Swagger docs: `http://localhost:8000/docs`).*

##### Terminal 2: Celery Worker
The worker processes document ingestion, OCR, and AI extraction from the `default` and `ocr` queues:

- **Linux / macOS:**
  ```bash
  cd backend
  celery -A app.workers.celery_app worker -Q default,ocr -c 2 --loglevel=INFO
  ```
- **Windows (PowerShell):**
  > **Crucial Windows Note:** Python on Windows does not support POSIX `fork`. You **must** specify the `solo` pool to prevent Celery process crashes:
  ```powershell
  cd backend
  celery -A app.workers.celery_app worker -Q default,ocr --pool=solo --loglevel=INFO
  ```

##### Terminal 3: Celery Beat Scheduler
Celery Beat is required for periodically relaying unpublished transactional outbox events to Redis and resetting stuck jobs:
```bash
cd backend
celery -A app.workers.celery_app beat --loglevel=INFO
```

#### Step 6: Frontend Setup & Dev Server

Open a 4th terminal to start the React application:

```bash
cd frontend

# Install Node dependencies
npm ci

# Start the Vite development server
npm run dev
```

*The frontend application is now live at **`http://localhost:5173`**.*  
The Vite dev server automatically proxies all `/api/*` network requests to `http://localhost:8000`.

---

### Option B: Containerized Deployment via Docker Compose

To run the entire system in isolated containers:

```bash
# 1. Setup environment file
cp .env.example .env

# 2. Build and launch all 7 services
docker compose up --build
```

### Docker Compose Architecture
The `docker-compose.yml` configures 7 orchestrated services:
1. `postgres`: PostgreSQL 16 with `pgvector` extension.
2. `redis`: Redis 7 instance with AOF persistence.
3. `migrate`: Ephemeral container running `alembic upgrade head` before services launch.
4. `api`: FastAPI application serving REST endpoints on port 8000.
5. `worker`: Celery worker listening on `default` and `ocr` queues.
6. `beat`: Singleton Celery Beat instance driving outbox delivery and stuck-job recovery.
7. `frontend`: Multi-stage built React SPA served via Nginx on port `8080`.

#### Bootstrap Admin in Docker
Once the containers are healthy, bootstrap your administrator user:
```bash
docker compose exec api python ../scripts/create_admin.py admin@example.com
```

- **Web UI:** `http://localhost:8080`
- **Backend API:** `http://localhost:8000`
- **Swagger Documentation:** `http://localhost:8000/docs`

---

## Environment Configuration Reference (.env)

| Environment Variable | Default | Purpose / Allowed Values |
|----------------------|---------|--------------------------|
| `DOCINT_ENVIRONMENT` | `development` | `development`, `test`, or `production`. In production, strict secret length checks are enforced. |
| `DOCINT_SECRET_KEY` | *(None)* | Required cryptographic secret for signing JWTs (must be $\ge 32$ characters in production). |
| `DOCINT_DATABASE_URL` | `postgresql+psycopg://...` | Connection URI for PostgreSQL database. |
| `DOCINT_REDIS_URL` | `redis://...` | Redis URL for Celery message broker and rate limiting. |
| `DOCINT_STORAGE_ROOT` | `/data/storage` | Local directory path for securely storing uploaded files. |
| `DOCINT_MAX_UPLOAD_BYTES` | `104857600` | Max file upload limit in bytes (Default: 100 MB). |
| `DOCINT_PDF_BATCH_PAGES` | `20` | Page count per PDF processing sub-job. Limits peak RAM usage. |
| `DOCINT_PIPELINE_VERSION` | `p1` | Bump this string to force reprocessing of cached idempotent stages. |
| `DOCINT_OCR_LANGUAGES` | `eng` | Tesseract language packs to use (e.g., `eng` or `eng+ben`). |
| `DOCINT_LLM_PROVIDER` | `mock` | `mock` (zero-cost deterministic test fixtures) or `openai_compatible`. |
| `DOCINT_LLM_BASE_URL` | *(Empty)* | Base URL for LLM provider (e.g., `https://api.openai.com/v1` or `http://localhost:11434/v1`). |
| `DOCINT_LLM_API_KEY` | *(Empty)* | API key for LLM provider (ignored when `provider=mock`). |
| `DOCINT_LLM_MODEL` | *(Empty)* | Primary extraction model identifier (e.g. `gpt-4o-mini`, `llama3`). |
| `DOCINT_LLM_FALLBACK_MODEL` | *(Empty)* | Secondary model invoked if the primary model fails schema repairs. |
| `DOCINT_LLM_JSON_MODE` | `true` | Send `response_format={"type": "json_object"}` with completions. |
| `DOCINT_EXTRACTION_MIN_CONFIDENCE` | `0.7` | Confidence score floor below which an extraction is flagged for human review. |
| `DOCINT_EXTRACTION_REVIEW_NON_INVOICES` | `true` | When `true`, documents classified as non-invoices are sent to human review. |
| `DOCINT_EMBEDDING_PROVIDER` | `mock` | `mock` (bag-of-words hash) or `sentence_transformers` (dense vectors). |
| `DOCINT_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Hugging Face embedding model name. |
| `DOCINT_EMBEDDING_DIM` | `384` | Dimension of embedding vectors (must match pgvector schema column). |
| `DOCINT_MONEY_TOLERANCE` | `0.01` | Maximum allowable arithmetic delta for Decimal financial rules. |

---

## API Architecture & Key Endpoints

The API is fully documented via interactive OpenAPI schemas at `/docs`. All primary business routes are prefixed under `/api/v1`.

### 1. System & Health
- `GET /health` - Liveness probe (checks process state).
- `GET /ready` - Readiness probe (verifies database & Redis connectivity).
- `GET /metrics` - Prometheus metrics (blocked from external ingress by Nginx).

### 2. Authentication & User Management
- `POST /api/v1/auth/login` - Authenticate via email/password; returns access token + rotating refresh token.
- `POST /api/v1/auth/refresh` - Exchange refresh token for a new token pair (detects token reuse and revokes compromised sessions).
- `POST /api/v1/auth/logout` - Revoke current refresh token session.
- `GET /api/v1/auth/me` - Retrieve current user profile and assigned role.
- `POST /api/v1/users` - Create a new user (`admin` only).
- `GET /api/v1/users` - List all system users (`admin` only).

### 3. Document Ingestion & Jobs
- `POST /api/v1/documents` - Upload document file (multipart/form-data with magic-byte verification).
- `GET /api/v1/documents` - Paginated document list with filters for status, filename, and date.
- `GET /api/v1/documents/{id}` - Retrieve document metadata and processing status.
- `GET /api/v1/documents/{id}/progress` - Real-time page progress (`pages_done / pages_total`).
- `GET /api/v1/documents/{id}/pages` - Page-level inspection records (kind, OCR confidence, status).
- `GET /api/v1/documents/{id}/pages/{page}/text` - Raw extracted text for a specific page.
- `GET /api/v1/documents/{id}/jobs` - List background jobs and sub-jobs with error traces.
- `POST /api/v1/jobs/{job_id}/retry` - Trigger a retry for a failed or dead-letter job.
- `POST /api/v1/jobs/{job_id}/cancel` - Cancel an in-flight job hierarchy.
- `GET /api/v1/documents/{id}/file` - Authenticated streaming download of the original document.

### 4. Invoices & Human Review
- `GET /api/v1/documents/{id}/invoices` - Invoices extracted from a specific document.
- `GET /api/v1/invoices/{id}` - Comprehensive invoice view (fields, line items, findings, confidence).
- `PUT /api/v1/invoices/{id}` - Apply audited field corrections (enforces optimistic locking via version tag).
- `POST /api/v1/findings/{id}/resolve` - Accept or dismiss a validation finding with a mandatory audit note.
- `POST /api/v1/invoices/{id}/review` - Approve or reject an invoice (approval blocked by open error findings).
- `GET /api/v1/review/queue` - Prioritized list of all invoices requiring human review.
- `GET /api/v1/invoices/{id}/corrections` - Audit log of all manual changes applied to the invoice.

### 5. Cross-Document Reconciliation
- `POST /api/v1/reconciliation` - Initiate reconciliation run between Left and Right document IDs.
- `GET /api/v1/reconciliation/{run_id}` - Retrieve detailed match report and discrepancy breakdown.
- `GET /api/v1/reconciliation/{run_id}/csv` - Export reconciliation discrepancies as CSV.
- `POST /api/v1/relationships/{id}/confirm` - Manually confirm a proposed match.
- `POST /api/v1/relationships/{id}/reject` - Reject a proposed match.

### 6. Search
- `GET /api/v1/search?q={query}&mode={hybrid|fts|vector}` - Hybrid search over indexed documents with RRF scoring.

---

## Code Quality & Runtime Verification

The repository has been streamlined for clean, lightweight local execution. All ad-hoc test scripts, browser automation runners, and Nginx reverse proxy configurations have been removed so the core application runtime is lean and focused.

### Frontend Type-Checking and Production Build

Verify strict TypeScript contracts and build the production bundle:

```bash
cd frontend

# Verify TypeScript types without emitting JavaScript
npm run typecheck

# Verify Vite production build
npm run build
```

### Backend Linting and Code Integrity

Check Python style and type adherence across all domain services:

```bash
cd backend

# Run Ruff linter across the application
ruff check app

# Check type annotations with Mypy
mypy app
```

---

## Troubleshooting & Senior Developer Field Notes

### 1. Celery on Windows (`ValueError` or Hanging Workers)
*Problem:* Running `celery worker` on native Windows hangs or fails with child process crashes.  
*Reason:* Windows does not support POSIX `fork()`, which Celery uses by default for prefork pooling.  
*Solution:* Always pass the `solo` pool on Windows:
```powershell
celery -A app.workers.celery_app worker -Q default,ocr --pool=solo --loglevel=INFO
```

### 2. Tesseract Not Recognized on Windows
*Problem:* Worker logs show `tesseract is not recognized as the name of a cmdlet`.  
*Solution:* Ensure Tesseract OCR is installed (e.g. at `C:\Program Files\Tesseract-OCR`) and the directory is added to your Windows System `PATH`. Verify by running `tesseract --version` in a new PowerShell prompt.

### 3. pgvector Extension Missing
*Problem:* Running `alembic upgrade head` fails with `type "vector" does not exist`.  
*Solution:* Ensure you are using a PostgreSQL image with pgvector installed (e.g. `pgvector/pgvector:pg16`). In your database console, verify that the extension can be created:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 4. Running Without an LLM API Key (`mock` Mode)
*Problem:* You want to develop locally without incurring API costs or waiting for an external LLM.  
*Solution:* Set `DOCINT_LLM_PROVIDER=mock` in your `.env`. The mock provider returns deterministic, schema-valid invoice fixtures keyed to the input hash. Scanned images and digital PDFs will be parsed, and spreadsheets/CSVs will be extracted deterministically via cell mappings.

### 5. Secret Key Validation Error in Production
*Problem:* App startup fails with `ValueError: DOCINT_SECRET_KEY must be at least 32 characters in production`.  
*Solution:* The system defensively prevents running production deployments with empty or trivial cryptographic keys. Generate a strong key using `python -c "import secrets; print(secrets.token_urlsafe(48))"` and set it in `.env`.

---

## Project Status & Roadmap

The system implementation status is tracked transparently in [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md).

### Implemented
- [x] Multi-format ingestion engine (PDF, Images, Excel, CSV) with magic-byte verification.
- [x] Page-batch processing sub-jobs with bounded peak memory.
- [x] Deterministic digital vs. scanned heuristic per page.
- [x] Computer vision image preprocessing (deskew, median filter, threshold) + Tesseract OCR.
- [x] Anti-hallucination evidence verification with quote validation.
- [x] Normalization engine (Bengali numerals, lakh/crore formats, ambiguous dates, multi-currency).
- [x] Deterministic `Decimal` validation engine (9 rule suites).
- [x] Cross-document reconciliation with discrepancy analysis and formula-injection-safe CSV export.
- [x] Hybrid search combining PostgreSQL FTS and pgvector embeddings via Reciprocal Rank Fusion.
- [x] Asynchronous job orchestration with Transactional Outbox pattern and Celery Beat relay.
- [x] Full REST API under `/api/v1` with RBAC, rotating refresh tokens, and rate limiting.
- [x] User management hardening: self-service password change, admin account deactivation/reset, and proxy-aware rate limiting (Phase 8b).
- [x] 10-page React 18 + TypeScript + Tailwind CSS review and management application.

### Next Steps & Production Hardening
- [ ] Connect production LLM endpoint and evaluate accuracy on a labeled real-world invoice corpus.
- [ ] Install and evaluate the Bengali (`ben`) Tesseract language model on native documents.
- [ ] Commit automated end-to-end browser regression tests using Playwright.
- [ ] Connect S3-compatible cloud storage adapter for distributed multi-node file access.

---

## License & Attribution

Designed and developed as an open modular architecture for enterprise document intelligence and automated financial reconciliation. Refer to [`docs/technical-decisions.md`](docs/technical-decisions.md) for architectural trade-off rationales.
