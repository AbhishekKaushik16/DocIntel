# DocIntel: Comprehensive System Architecture

This document provides a 0-to-100% deep dive into every piece of the DocIntel repository, mapping the journey of a document from a raw PDF upload to a highly structured, queryable data point.

---

## 1. System Overview (The 10,000-foot View)

DocIntel is a 6-tier distributed system orchestrated via Docker Compose. It leverages an asynchronous, event-driven architecture to ensure the UI remains responsive even when processing massive 100+ page documents.

### The Docker Compose Stack (`docker-compose.yml`)
1. **Frontend**: Next.js App Router (Port 3000)
2. **Backend**: FastAPI web server (Port 8000)
3. **Worker**: Celery background worker
4. **Redis**: Message broker and result backend for Celery
5. **PostgreSQL**: Primary transactional database and JSONB store
6. **Elasticsearch**: Analytical search engine and NoSQL index

### The Data Flow
`Upload (UI)` $\rightarrow$ `FastAPI (Save state)` $\rightarrow$ `Redis (Queue task)` $\rightarrow$ `Celery Worker (Parse & Extract)` $\rightarrow$ `Postgres & Elasticsearch (Save JSON)` $\rightarrow$ `UI (Status Complete)` $\rightarrow$ `Query Agent (Chat)`

---

## 2. Frontend Layer (Next.js)

Located in `/frontend`. Built using **React 18**, **Next.js (App Router)**, **Tailwind CSS**, and **TypeScript**.

### Directory Structure
*   `src/app/`: Defines the Next.js routes (`/`, `/documents`, `/query`, `/review/[id]`).
*   `src/components/`: Reusable UI components.
    *   `DocumentUpload.tsx`: Handles drag-and-drop file uploads.
    *   `DocumentList.tsx`: Displays the real-time status of documents using staggered polling/WebSockets.
    *   `QueryChat.tsx`: The chat interface for the Agentic AI.
    *   `ReviewInterface.tsx`: The Human-In-The-Loop (HITL) UI for correcting low-confidence extractions.
*   `src/lib/`: API client utilities and type definitions.

### Key UX Decisions
*   **Decoupled State**: The frontend never waits synchronously for a document to process. It receives a `pending` status immediately and updates the UI asynchronously.
*   **Conversational Chat**: `QueryChat.tsx` maintains a rolling array of `chat_history` payload to enable the AI to remember pronouns and previous context.

---

## 3. API Layer (FastAPI)

Located in `/backend/app/api/`. The primary gateway for the frontend.

### Tech Stack
*   **FastAPI**: High-performance async web framework.
*   **Pydantic**: Enforces strict typing on all incoming/outgoing payloads.
*   **SQLAlchemy 2.0**: Asynchronous ORM for Postgres interaction.

### Core Routers
*   `GET /api/health`: Standard readiness probe.
*   `POST /api/documents/upload`: Saves the raw file to disk/volume, writes a `PENDING` row to Postgres, and fires a Celery `delay()` task.
*   `GET /api/documents/{id}`: Fetches the extracted JSON data for the review UI.
*   `POST /api/query`: Ingests natural language questions and `chat_history`, routes them to the LangChain ReAct agent, and returns the synthesized answer.

---

## 4. Asynchronous Pipeline (Celery + Redis)

Located in `/backend/app/pipeline/`. This is the heavy-lifting engine of DocIntel.

Since LLM calls and OCR are fundamentally slow, processing is entirely offloaded to background workers. The pipeline consists of 4 strictly defined stages:

1.  **Classify (`classifier.py`)**: Determines if the document is an Invoice, Receipt, Contract, or Resume.
2.  **Parse (`parser.py`)**: Extracts raw text using PyPDF2 or Tesseract OCR for images. Handles document chunking for massive files.
3.  **Extract (`extractor.py`)**: The core AI logic. Uses LangChain and Instructor to force the LLM to return strictly typed JSON that maps to our Pydantic models. Includes a dual-mode fallback (if OpenRouter fails due to 402 errors, it instantly falls back to Google Gemini).
4.  **Validate & Score**: Analyzes the extracted JSON, calculates a confidence score (e.g., verifying if invoice line items sum to the total), and sets the status to `COMPLETED` or `NEEDS_REVIEW`.

---

## 5. Persistence Layer (PostgreSQL)

Located in `/backend/app/models/` and `/backend/app/database.py`.

### The Hybrid Data Model
We use a single powerful database to handle two very different types of data:
1.  **Relational State**: The `documents` table stores strictly typed metadata (`status`, `document_type`, `original_filename`, `confidence_score`).
2.  **Unstructured Data**: The `extracted_data` column is a `JSONB` field. This allows us to store wildly different schemas (an Invoice looks nothing like a Resume) in the same table without requiring database migrations.
3.  **Fast Keyword Filtering**: We utilize Postgres `tsvector` and GIN indexing on the raw text for lightning-fast keyword matching when building SQL queries.

---

## 6. Search & Analytics Layer (Elasticsearch)

Located in `/backend/app/elasticsearch.py`. 

While Postgres handles the relational state, Elasticsearch is utilized for deep analytical queries over the unstructured JSON.

### 1. The Mapping Explosion Fix
LLMs are unpredictable and occasionally hallucinate data types (e.g., returning an `address` as a string today, and a nested JSON object tomorrow). This traditionally crashes Elasticsearch (a "Mapping Explosion"). 
**Architecture Fix**: We defined the `extracted_data` field mapping as `flattened` in Elasticsearch. This prevents ES from dynamically indexing every unpredictable nested sub-field, protecting the cluster's stability while still allowing leaf-node searches.

### 2. Semantic Hybrid Search (kNN)
To allow users to query documents conceptually (e.g. *"contracts about liability caps"*) rather than relying purely on exact keyword matches, we implemented native Semantic Search:
*   **Embeddings**: During the Celery extraction stage, a semantic summary of the document's content and structured JSON is embedded into a 768-dimensional vector using Gemini (`models/text-embedding-004`) or OpenAI (`text-embedding-3-small`).
*   **Vector Indexing**: The embedding is saved to the `document_vector` field in ES, which is mapped as a `dense_vector` with cosine similarity indexing.
*   **Hybrid Queries**: When the `search_documents` LangChain tool is invoked, the user's natural language query is embedded. Elasticsearch natively executes a hybrid search by combining a `knn` vector query (for conceptual relevance) alongside a `multi_match` boolean query (for exact keyword/filename matches).
---

## 7. The Agentic Query Engine (LangChain)

Located in `/backend/app/pipeline/query_agent.py`.

This is not a simple RAG (Retrieval-Augmented Generation) system. Standard RAG fails at answering analytical questions (like *"What is the total revenue across all invoices?"*). 

We built a **ReAct (Reasoning + Acting) Agent**:
*   **The Brain**: Uses OpenAI `gpt-4o` or Gemini `2.5-flash-lite`.
*   **The Tools**: The agent is equipped with a toolbox of Python functions it can call autonomously:
    1.  `search_documents`: Used when the user asks semantic or keyword questions.
    2.  `query_jsonb`: The agent writes valid PostgreSQL JSON path queries to filter documents based on deeply nested extracted data.
    3.  `aggregate_stats`: The agent writes SQL aggregation commands (`COUNT`, `AVG`, `SUM`, `GROUP BY`) to answer mathematical questions about the dataset.

When a user asks a question, the Agent *reasons* about which tool to use, *acts* by executing the tool against the Postgres/Elasticsearch database, observes the result, and synthesizes a final natural language answer.
