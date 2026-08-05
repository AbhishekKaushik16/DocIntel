# Architectural Decisions & Trade-Off Log (`decisions.md`)

This document records the architectural and design decisions made during the 5-day build of the **Document Intelligence Platform** (Problem Statement 3: *"Turn messy documents into structured, queryable data"*).

---

## Decision 1: PostgreSQL (JSONB + tsvector) vs MongoDB vs Polyglot Search

### The Decision
We chose **PostgreSQL 16** with a hybrid data model:
1. Standard relational tables for document state, processing audit logs, and normalized extracted fields.
2. `JSONB` column for dynamic per-type extracted structures.
3. `tsvector` + GIN indexing for built-in Full-Text Search (FTS).

### Alternatives Considered
- **MongoDB (Pure Document Database)**: Considered because invoice/resume extractions fit MongoDB's native JSON document model cleanly.
- **Dual-DB (PostgreSQL + Elasticsearch / Meilisearch)**: Considered for production-grade typo tolerance and faceted search.
- **SQLite (with JSON1 + FTS5)**: Considered for zero-dependency local execution.

### Reasoning & Trade-offs
- **Why Postgres won**: In a document intelligence pipeline, data falls into two distinct categories: *relational pipeline metadata* (status state machine, audit logs, confidence scores) and *semi-structured document extractions*. PostgreSQL supports both natively in a single container. Its `tsvector` FTS with GIN indexing eliminates the need to run and sync a separate search daemon (like Elasticsearch).
- **Trade-offs accepted**: Schema validation on `JSONB` columns must be handled at the application layer using Pydantic schemas rather than database-level constraints. Complex JSON array query syntax (`jsonb_path_query`) is slightly more verbose than MongoDB query syntax.

---

## Decision 2: 4-Stage Async Pipeline with Celery vs Direct Synchronous API Extraction

### The Decision
We implemented a **4-stage decoupled asynchronous processing pipeline** (Classify $\rightarrow$ Parse $\rightarrow$ Extract $\rightarrow$ Validate) backed by **Celery + Redis** with WebSockets for real-time UI updates.

### Alternatives Considered
- **Synchronous HTTP Request/Response**: Process the document inside the `POST /api/documents/upload` route handler.
- **FastAPI BackgroundTasks**: Light async background task execution without Redis.

### Reasoning & Trade-offs
- **Why Celery + 4 Stages won**: OCR and LLM API calls are slow and can fail or hit rate limits. Blocking the HTTP upload request leads to timeouts and poor UX. By decoupling into 4 distinct logged stages:
  1. *Classify*: Determines format and document type (invoice, receipt, contract, resume, generic).
  2. *Parse*: Formats PDF, images, DOCX, CSV into clean text (with Tesseract OCR fallback).
  3. *Extract*: LLM schema extraction or regex fallback.
  4. *Validate*: Calculates composite confidence scores & cross-field consistency checks.
- **Trade-offs accepted**: Requires running Redis and Celery worker processes alongside the FastAPI server.

---

## Decision 3: Multi-Mode Extractor (LLM Structured Outputs + Dual-Mode Fallback)

### The Decision
We built a **hybrid extraction engine**:
1. **Primary**: OpenAI `gpt-4o-mini` with Pydantic JSON mode schema enforcement.
2. **Fallback**: Rule-based regex extractor for dates, currency amounts, emails, phone numbers, and URLs when no API key is provided or when rate limits/network errors occur.

### Alternatives Considered
- **LLM-only**: Reject documents if no OpenAI API key is set.
- **Local Open-Source LLM (Ollama/vLLM)**: Run Qwen-VL or Llama 3 locally.

### Reasoning & Trade-offs
- **Why Dual-Mode won**: Real-world software must degrade gracefully. If a evaluator tests the repo without providing an OpenAI API key, the system still operates using regex heuristics rather than crashing with 500 errors.
- **Why local LLMs were rejected**: Running vLLM/Ollama requires 8GB+ GPU VRAM, which creates friction for evaluators running `docker-compose up` on standard laptops.

---

## Decision 4: Confidence Scoring & Human-in-the-Loop (HITL) Workflow

### The Decision
We built an automated **confidence scoring engine** that routes documents into three status buckets:
- `confidence >= 0.8` $\rightarrow$ `completed` (auto-approved)
- `0.5 <= confidence < 0.8` $\rightarrow$ `needs_review` (flagged for human review)
- `confidence < 0.5` $\rightarrow$ `failed`

We created a dedicated **Human Review Interface** in the UI where users can edit and save corrected fields, updating the audit trail.

### Alternatives Considered
- Direct auto-approval of all LLM extractions without scoring or human review.

### Reasoning & Trade-offs
- **Why HITL won**: This directly addresses the **"Above and Beyond"** evaluation criterion (*"You solved a hard sub-problem others avoid"*). LLMs hallucinate and OCR misreads characters. Building a feedback loop where humans can verify and correct low-confidence extractions makes the system production-grade.
- **Cross-validation math**: Invoices check if `total_amount ≈ subtotal + tax_amount` and `total_amount ≈ sum(line_items)`. Passing these rules boosts the confidence score.

---

## What We Deliberately Cut (and Why)

| Cut Feature | Reason for Cutting |
| :--- | :--- |
| **Multi-Tenant Authentication & User Auth** | Adds zero value to the core technical problem of document extraction and queryability. We focused 100% of our velocity on pipeline depth and UX polish. |
| **Vector / Semantic Search (pgvector)** | PostgreSQL `tsvector` full-text search already fulfills the search and query requirement cleanly. Adding vector embeddings increases build time and LLM costs without adding fundamental utility to structured JSON data queries. |
| **Custom Schema Builder** | Allowing users to define arbitrary extraction templates on the fly is a complex product feature. We supported 5 rich pre-built domain schemas (Invoice, Receipt, Contract, Resume, Generic) instead. |
