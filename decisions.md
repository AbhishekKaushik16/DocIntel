# Architectural Decisions (`decisions.md`)

This is a running log of the real calls we made while building DocIntel, focusing on judgment under ambiguity and time pressure.

---

## 1. Database Architecture: PostgreSQL Hybrid

*   **The decision:** We chose PostgreSQL 16 using a hybrid model: relational tables for state/metadata, `JSONB` for dynamically extracted fields, and `tsvector` for full-text search.
*   **The alternatives:** MongoDB (good for JSON, bad for relational state/search), or a dual-DB setup like PostgreSQL + Elasticsearch.
*   **The reasoning:** Document intelligence requires tracking a strict state machine (Processing $\rightarrow$ Needs Review $\rightarrow$ Completed) alongside highly unstructured extracted data. Postgres handles both perfectly. Using `JSONB` avoids schema migrations when new document types are added.
*   **What you deliberately cut:** We deliberately cut a dedicated Vector DB (like Pinecone) or pgvector. We realized that querying extracted financial data requires SQL-like aggregations and exact keyword matches, not semantic similarity. A vector search would add latency and cost without actually solving the problem of querying structured tabular data.

---

## 2. Processing Architecture: Decoupled Async Pipeline

*   **The decision:** We built a decoupled asynchronous pipeline using Celery + Redis, moving all document processing out of the main web thread.
*   **The alternatives:** Synchronous HTTP request processing (e.g., awaiting the LLM call inside the FastAPI `/upload` endpoint), or using FastAPI `BackgroundTasks`.
*   **The reasoning:** LLM API calls and OCR are fundamentally slow (often taking 3-4 minutes for 100-page PDFs) and prone to rate-limiting. A synchronous HTTP request would inevitably timeout in the browser. Celery allows us to implement retries, handle timeouts gracefully, and process large queues concurrently without crashing the API.
*   **What you deliberately cut:** We deliberately cut real-time synchronous extraction. The UX trade-off is that users have to wait a few minutes and rely on a polling/WebSocket status update rather than getting an instant response, but the reliability gained for massive documents is worth it.

---

## 3. Resilience: Multi-Provider LLM Fallback

*   **The decision:** We implemented an abstraction layer that allows the system to seamlessly swap between OpenAI (via OpenRouter) and Google Gemini based on `.env` configuration or runtime errors.
*   **The alternatives:** Hardcoding the OpenAI SDK and failing the pipeline if OpenAI is down or out of credits.
*   **The reasoning:** We hit real-world failure modes immediately: OpenRouter threw `402 Payment Required` errors when we ran out of credits during a 114-page PDF extraction. By abstracting the LLM client, we caught the error, saved partial progress, and instantly failed over to a cheaper Gemini model without having to rewrite the extraction logic.
*   **What you deliberately cut:** We cut support for local open-source models (like Ollama/vLLM). While running Llama 3 locally is cheaper, it requires 8GB+ GPU VRAM, making it impossible for a reviewer to run the stack via `docker-compose up` on a standard laptop.

---

## 4. Query Engine: Agentic LangChain vs Naïve RAG

*   **The decision:** We built a conversational Query Agent using LangChain equipped with custom tools (`search_documents`, `query_jsonb`, `aggregate_stats`).
*   **The alternatives:** A standard RAG (Retrieval-Augmented Generation) pipeline that simply chunks text, embeds it, and injects the top 5 chunks into an LLM prompt.
*   **The reasoning:** Standard RAG is terrible at analytical questions like *"How many invoices did Amazon send?"* or *"What is the average confidence score?"*. By giving the LLM agent tools to write SQL/JSONB queries against the Postgres database and aggregations, the system can autonomously route semantic questions to full-text search and analytical questions to the database. We also added conversational memory so users can ask follow-ups.
*   **What you deliberately cut:** We cut complex cross-document semantic synthesis (e.g. comparing the abstract meaning of two 100-page contracts). We optimized the agent specifically for querying the *structured data* we worked so hard to extract.

---

## 5. UI/UX: Confidence Scoring & Human-in-the-Loop

*   **The decision:** We implemented an automated confidence scoring engine that routes extractions into a "Needs Review" state if the LLM confidence is `< 0.8`.
*   **The alternatives:** Auto-approving all LLM extractions and assuming they are 100% correct.
*   **The reasoning:** LLMs hallucinate, and OCR misreads numbers. In an enterprise setting, an incorrect invoice extraction can cost thousands of dollars. We explicitly designed a Human-in-the-Loop (HITL) UI where users can audit flagged documents and correct the JSON before it is finalized. 
*   **What you deliberately cut:** We cut complex automated cross-validation math (e.g., using a Python solver to verify if line items sum exactly to the total amount considering tax variations). We opted for a simpler heuristic score combined with human review to maintain development velocity.
