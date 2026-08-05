# DocIntel — Document Intelligence Platform

> **Turn messy documents into structured, queryable data.**
> Built for Problem Statement 3 of the Zamp Engineering Challenge.

DocIntel is an end-to-end web application that ingests unstructured and semi-structured documents (PDFs, scanned images, DOCX, CSV, TXT), automatically classifies them, extracts clean structured JSON data, scores confidence, provides a human-in-the-loop review interface, and powers full-text & faceted search across all extracted records.

---

## 🌟 Key Features

- **Multi-Format Ingestion**: Supports PDF (text & scanned), Images (PNG, JPG, TIFF), DOCX, CSV, Excel, TXT, and Markdown.
- **4-Stage Processing Pipeline**:
  1. **Classify**: Intelligent document type classification (Invoice, Receipt, Contract, Resume, Generic).
  2. **Parse**: Hybrid PyMuPDF + Tesseract OCR layout preservation.
  3. **Extract**: OpenAI structured JSON extraction with Pydantic schema enforcement.
  4. **Validate**: Field completeness, cross-field consistency checks (e.g., invoice total validation), and composite confidence scoring.
- **Graceful Degradation**: Dual-mode extraction engine — if no OpenAI API key is configured, falls back seamlessly to rule-based regex extraction.
- **Human-in-the-Loop Review**: Automated routing of low-confidence extractions (`0.5 <= confidence < 0.8`) to a dedicated review interface for manual field verification.
- **Full-Text & Faceted Search**: Powered by PostgreSQL `tsvector` + GIN indexing with query highlighting and relevance ranking.
- **Full Observability**: Comprehensive audit trail logging every stage, duration, warnings, and error messages per document.

---

## 🏗 Architecture & Technology Stack

```
[ Frontend: Next.js 15 + React 19 + Tailwind CSS ]
                       │ (REST API)
                       ▼
[ Backend: FastAPI (Python 3.12) + SQLModel / SQLAlchemy ]
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
[ PostgreSQL 16 (JSONB + FTS) ]  [ Redis + Celery Task Queue ]
                                     │ (Async Processing)
                                     ▼
                      [ Pipeline: Parse → Classify → Extract → Validate ]
```

| Layer | Technology |
| :--- | :--- |
| **Frontend** | Next.js 15 (App Router), TypeScript, Tailwind CSS, Lucide Icons |
| **Backend API** | FastAPI, Pydantic v2, SQLAlchemy (Async), asyncpg |
| **Task Queue** | Celery + Redis |
| **Database** | PostgreSQL 16 (JSONB for dynamic schemas, `tsvector` for FTS) |
| **OCR & Parsing** | PyMuPDF, pytesseract (Tesseract OCR), python-docx, Pandas |
| **LLM Engine** | OpenAI `gpt-4o-mini` (structured output JSON mode) |

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
│   │   │   ├── classifier.py   # Stage 1: Document classification
│   │   │   ├── parser.py       # Stage 2: Layout parsing & OCR
│   │   │   ├── extractor.py    # Stage 3: LLM & regex extraction
│   │   │   ├── validator.py    # Stage 4: Confidence scoring & rules
│   │   │   └── orchestrator.py # Pipeline coordinator
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
