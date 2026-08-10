# DocIntel — Document Intelligence Platform

> **Turn messy documents into structured, queryable data.**
> Built for Problem Statement 3 of the Zamp Engineering Challenge.

DocIntel is an end-to-end web application that ingests unstructured and semi-structured documents (PDFs, scanned images, DOCX, CSV, TXT), automatically classifies them, extracts clean structured JSON data, scores confidence, provides a human-in-the-loop review interface, and powers full-text & faceted search across all extracted records.

---

## 🌟 Key Features

- **Multi-Format Ingestion**: Supports PDF (text & scanned), Images (PNG, JPG, TIFF), DOCX, CSV, Excel, TXT, and Markdown.
- **5-Stage Agentic Pipeline (LangGraph)**:
  1. **Classify**: Intelligent document type classification.
  2. **Parse**: Hybrid PyMuPDF + Tesseract OCR layout preservation.
  3. **Extract**: Structured JSON extraction with schema enforcement.
  4. **Validate**: Field completeness and cross-field consistency checks.
  5. **Resolve**: ReAct agent that automatically corrects validation errors (e.g., math mistakes).
- **Graceful Degradation**: Dual-mode extraction engine — if no OpenAI API key is configured, falls back seamlessly to rule-based regex extraction.
- **Human-in-the-Loop Review**: Automated routing of low-confidence extractions (`0.5 <= confidence < 0.8`) to a dedicated review interface for manual field verification.
- **Hybrid Full-Text & Vector Search**: Powered by Elasticsearch (`flattened` mapping for dynamic JSON, `dense_vector` for semantic similarity search).
- **Agentic Orchestration**: Uses LangGraph with `langgraph-checkpoint-postgres` for fault-tolerant state persistence. Celery workers run with `task_acks_late=True` for bulletproof retries.

---

## 🏗 Architecture & Technology Stack

```
[ Frontend: Next.js 15 + React 19 + Tailwind CSS ]
                       │ (REST API)
                       ▼
[ Backend: FastAPI (Python 3.12) ]
                       │
          ┌────────────┼─────────────┐
          ▼            ▼             ▼
  [ PostgreSQL ]  [ Redis ]   [ Elasticsearch ]
 (State Checkpoint) (Celery)  (Hybrid Search)
                       │
                       ▼
       [ LangGraph 5-Stage Agentic Pipeline ]
```

| Layer | Technology |
| :--- | :--- |
| **Frontend** | Next.js 15 (App Router), TypeScript, Tailwind CSS, Lucide Icons |
| **Backend API** | FastAPI, Pydantic v2, SQLAlchemy (Async), asyncpg |
| **Task Queue** | Celery + Redis |
| **Database** | PostgreSQL 16 (LangGraph checkpointing & relations) |
| **Search Engine**| Elasticsearch 8.15 (`flattened` mapping, 768-dim `dense_vector`) |
| **OCR & Parsing** | PyMuPDF, pytesseract, python-docx, Pandas |
| **LLM Engine** | LangGraph, Gemini `gemini-3-flash`, `models/gemini-embedding-2` |

---

## 🚀 Quick Start (Running via Docker Compose)

The easiest way to run DocIntel is using Docker Compose:

### 1. Clone & Configure Environment
```bash
git clone <repo-url>
cd zamp

# Optional: Add your OpenAI API key for LLM extraction
# (If left empty, DocIntel automatically runs in regex fallback mode)
cp backend/.env.example backend/.env
```

### 2. Start all services
```bash
make dev
# or
docker-compose up -d --build
```

### 3. Open in Browser
- **Frontend Dashboard**: [http://localhost:3000](http://localhost:3000)
- **FastAPI OpenAPI Specs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **API Health Check**: [http://localhost:8000/api/health](http://localhost:8000/api/health)

---

## 🧪 Running Tests

DocIntel includes unit tests for all pipeline stages and integration tests for the API:

```bash
# Run tests inside Docker container
make test

# Or run tests locally with pytest
cd backend && pytest -v --cov=app tests/
```

---

## 📂 Project Structure

```
zamp/
├── backend/
│   ├── app/
│   │   ├── api/            # Route handlers (documents, search)
│   │   ├── models/         # SQLAlchemy database models & schemas
│   │   ├── pipeline/       # 4-stage processing pipeline
│   │   │   ├── classifier.py   # Stage 1: Classification
│   │   │   ├── parser.py       # Stage 2: Parsing & OCR
│   │   │   ├── extractor.py    # Stage 3: Extraction
│   │   │   ├── validator.py    # Stage 4: Validation
│   │   │   ├── resolver.py     # Stage 5: ReAct Agent Resolution
│   │   │   ├── graph.py        # LangGraph definitions
│   │   │   └── orchestrator.py # Celery task entry point
│   │   ├── config.py       # Pydantic environment settings
│   │   ├── database.py     # Asyncpg connection session pool
│   │   └── main.py         # FastAPI application entry point
│   ├── tests/              # Pytest test suite
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/            # Next.js App Router (Dashboard, Docs, Detail, Search)
│   │   ├── components/     # React UI components
│   │   └── lib/            # API client
│   └── Dockerfile
├── decisions.md            # Required decision log
├── docker-compose.yml      # Service orchestration (Postgres, Redis, API, Worker, UI)
├── Makefile                # Convenience targets
└── README.md
```

---

## 📄 Decisions & Trade-Off Log

See [`decisions.md`](file:///Users/abhishek/workplace/zamp/decisions.md) for a detailed rationale on architectural choices, alternatives considered (Postgres vs MongoDB vs SQLite), trade-offs accepted, and scope decisions.
