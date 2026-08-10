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

---

## 7. Clarifications & Ambiguities

The problem statement was intentionally open-ended: *"Turn messy documents into structured, queryable data"*. Here are the key ambiguities we identified and the calls we made:

*   **Ambiguity 1: What defines a "messy document" and "structured data"?**
    *   *Our Call:* We decided against hardcoding a rigid schema for a single document type (like just parsing invoices). Instead, we interpreted "structured data" as a completely dynamic requirement. Our pipeline uses an LLM to dynamically determine the document type and extract an arbitrary, highly-nested JSON schema that fits the specific document, whether it's an invoice, a resume, or a 100-page contract.
*   **Ambiguity 2: What does "queryable" actually mean?**
    *   *Our Call:* "Queryable" usually implies basic keyword search (like `LIKE %term%`). We decided that true document intelligence requires multiple axes of querying. We implemented **hybrid semantic search** (via Elasticsearch) for conceptual questions, and paired it with an **agentic tool-use layer** that can execute exact JSONB lookups and mathematical aggregations (e.g., *"What is the average total of all invoices?"*) directly against the structured data in Postgres.
*   **Ambiguity 3: What happens when the extraction is wrong?**
    *   *Our Call:* Messy documents lead to messy extractions. Rather than forcing a human to review every single math mistake the LLM makes, we interpreted this as an opportunity for automation. We built a validation layer and an autonomous ReAct Resolver Agent to mathematically prove and fix errors (like a hallucinated tax digit) before it ever hits the database.
*   **Ambiguity 4: Is a "system" just an API or a full product?**
    *   *Our Call:* An API is a utility, not a product. We interpreted the prompt as a challenge to build a delightful end-to-end user experience. We built a full Next.js frontend with visual status badges, an Agent Trace Viewer for observability, and real-time SSE streaming for the conversational AI query interface to remove perceived latency.
*   **Ambiguity 5: Should the system include Multi-Tenant User Authentication?**
    *   *Our Call:* We deliberately scoped out login screens, JWTs, and multi-tenant data isolation. While building row-level security in Postgres and filtered indices in Elasticsearch is important for production, it is fundamentally a solved engineering problem (mostly boilerplate). Given the 5-day constraint, we chose to optimize for **depth over breadth**. We invested that time into solving the genuinely hard, novel sub-problems (like the autonomous ReAct Resolver Agent and SSE LLM streaming) rather than building a standard login flow that reviewers have seen a thousand times.
*   **Ambiguity 6: Does "handling the real world" mean scaling to concurrent users?**
    *   *Our Call:* Yes. A naïve solution processes documents synchronously and falls over if two users upload 100-page PDFs at the same time. We engineered for true concurrency. By decoupling the API from the heavy LLM/OCR processing via **Celery and Redis**, the FastAPI backend instantly absorbs massive load spikes. Our Celery workers process documents in parallel, and because we use `langgraph-checkpoint-postgres`, the state of every single document's pipeline is written transactionally to Postgres, preventing race conditions or memory thrashing under load.
