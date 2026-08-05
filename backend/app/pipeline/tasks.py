"""
Celery tasks for background document processing.

Uses an async bridge to run the async pipeline orchestrator
from within Celery's synchronous task execution model.

Retry semantics
───────────────
Only *transient* infrastructure errors (DB unavailable, network blip) trigger a
retry. Logic errors and "already processing" no-ops raise without retrying so we
don't waste worker slots on documents that genuinely can't be processed right now.
"""

import asyncio
import logging

from celery import Celery

from app.config import settings

logger = logging.getLogger(__name__)

# ── Celery app ─────────────────────────────────────────────────────────────────

celery_app = Celery(
    "docintel",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,           # Don't ack until task completes (prevents lost tasks)
    worker_prefetch_multiplier=1,  # Process one task at a time per worker
    task_soft_time_limit=300,      # 5-minute soft limit  → SoftTimeLimitExceeded raised
    task_time_limit=360,           # 6-minute hard limit  → SIGKILL
    # Exponential backoff for retries: 30s, then 60s
    task_default_retry_delay=30,
)


# ── Async bridge ───────────────────────────────────────────────────────────────

def _run_async(coro):
    """Run an async coroutine in a fresh event loop (Celery worker bridge)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# ── Transient error types that warrant a retry ────────────────────────────────

_TRANSIENT_ERRORS = (
    # SQLAlchemy / asyncpg connection errors
    "ConnectionRefusedError",
    "asyncpg.exceptions.ConnectionDoesNotExistError",
    "sqlalchemy.exc.OperationalError",
    # Redis / Celery broker blips
    "kombu.exceptions.OperationalError",
    "redis.exceptions.ConnectionError",
    # Network timeouts talking to the LLM provider
    "httpx.TimeoutException",
    "httpx.ConnectError",
)


def _is_transient(exc: Exception) -> bool:
    """Return True if the exception looks like a recoverable infrastructure error."""
    exc_type = type(exc).__name__
    exc_module = type(exc).__module__ or ""
    full_name = f"{exc_module}.{exc_type}"
    return any(
        t in exc_type or t in full_name
        for t in _TRANSIENT_ERRORS
    )


# ── Task ───────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="process_document",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def process_document_task(self, document_id: str):
    """
    Celery task that processes a document through the full pipeline.

    Retries up to 3 times with 30-second delays, but ONLY for transient
    infrastructure errors. Logic errors (document not found, already processing,
    bad file format) do not trigger retries.
    """
    from app.pipeline.orchestrator import process_document

    try:
        _run_async(process_document(document_id))

    except Exception as exc:
        if _is_transient(exc):
            # Exponential back-off: 30 s → 60 s → 120 s
            delay = 30 * (2 ** self.request.retries)
            logger.warning(
                "Transient error processing document %s (attempt %d/%d): %s — retrying in %ds",
                document_id, self.request.retries + 1, self.max_retries + 1, exc, delay,
            )
            raise self.retry(exc=exc, countdown=delay)
        else:
            # Non-transient: log and let it fail without retrying.
            # The orchestrator's catch-all already set the DB status to FAILED.
            logger.error(
                "Non-transient error processing document %s: %s",
                document_id, exc, exc_info=True,
            )
            raise
