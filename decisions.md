# Architectural Decisions (`decisions.md`)

This is a running log of the real calls we made while building DocIntel, focusing on judgment under ambiguity and time pressure.

---

## 1. Database Architecture: PostgreSQL + Elasticsearch Hybrid

*   **The decision:** We chose PostgreSQL 16 for robust relational state and LangGraph checkpointing, paired with Elasticsearch 8.15 for hybrid vector and full-text search.
*   **The alternatives:** Relying purely on Postgres `tsvector` and `pgvector`, or using a dedicated vector database like Pinecone.
*   **The reasoning:** Document intelligence requires both strict state machine tracking (Postgres) and highly flexible schema-less search. We initially tried Postgres `JSONB` for search, but hit mapping explosion issues with deeply nested extracted data. Elasticsearch's `flattened` mapping solved the explosion, while its `dense_vector` support enabled hybrid semantic search natively.
*   **What you deliberately cut:** We deliberately cut a dedicated standalone Vector DB (like Pinecone) to minimize infrastructure complexity, choosing Elasticsearch which perfectly handles both our full-text highlighting and dense vector needs in one service.

---

## 2. Processing Architecture: LangGraph Agentic Pipeline

*   **The decision:** We built a 5-stage decoupled asynchronous pipeline using Celery + Redis, orchestrated internally by **LangGraph** with state checkpointing via `langgraph-checkpoint-postgres`.
*   **The alternatives:** A hardcoded linear Celery chain, or synchronous HTTP request processing.
*   **The reasoning:** LLM API calls and OCR are fundamentally slow and prone to rate-limiting or random API drops. Standard Celery chains fail entirely on node crashes. By wrapping the pipeline in LangGraph, we achieved true stateful resumption—if a worker dies during the Extraction stage, it reloads the exact state graph from Postgres and resumes without needing to re-run OCR or Classification.
*   **What you deliberately cut:** We deliberately cut real-time synchronous extraction. The UX trade-off is that users have to wait and rely on polling, but the bulletproof reliability gained for massive documents is worth it.

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

## 5. Resilience & Automation: The ReAct Resolver Agent

*   **The decision:** Instead of solely relying on humans to fix validation errors, we introduced a 5th pipeline stage: a **Resolver Agent**.
*   **The alternatives:** Hard-failing documents into a "Needs Review" queue the second a validation rule (e.g., "Subtotal + Tax != Total") fails.
*   **The reasoning:** LLMs often make trivial math mistakes or hallucinate a digit. Forcing a human to review every minor hallucination defeats the purpose of automation. The Resolver Agent receives the exact validation errors and the raw text, uses a ReAct loop to diagnose the failure, and autonomously patches the JSON payload. It has a strict retry limit to prevent infinite loops.
*   **What you deliberately cut:** We still kept the Human-in-the-Loop (HITL) UI for low-confidence scores (`< 0.8`), but we cut out human intervention for deterministic validation failures that the agent can mathematically prove and fix itself.

---

## 6. UX Depth: Real-Time SSE Streaming for Query Agent

*   **The decision:** We overhauled the backend query API and the React frontend to use Server-Sent Events (SSE) combined with a custom `ReadableStream` parser to stream the AI's response dynamically.
*   **The alternatives:** A standard synchronous HTTP request that forces the user to wait 10-15 seconds while the agent fetches tools and generates a complete response.
*   **The reasoning:** We wanted to prioritize a delightful end-to-end user journey. When an LLM executes tools (like querying a database), the latency is massive. By implementing streaming, we provide immediate visual feedback (the tool logs stream in instantly), and the user sees the answer being typed out word-by-word. We even implemented a resilient fallback in the frontend parser to ensure the UI doesn't break if the AI fails to perfectly format its XML response.
*   **What you deliberately cut:** We chose to use plain React `ReadableStream` logic rather than adopting heavy real-time websocket frameworks like Socket.io or GraphQL subscriptions, minimizing our backend dependency footprint while still delivering a highly interactive experience.
