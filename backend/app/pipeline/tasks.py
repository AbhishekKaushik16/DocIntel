"""
Celery tasks for background document processing.

Uses an async bridge to run the async pipeline orchestrator
from within Celery's synchronous task execution model.
"""

import asyncio

from celery import Celery

from app.config import settings

# Create Celery app
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
    task_acks_late=True,  # Don't ack until task completes (prevents lost tasks)
    worker_prefetch_multiplier=1,  # Process one task at a time per worker
    task_soft_time_limit=300,  # 5-minute soft limit
    task_time_limit=360,  # 6-minute hard limit
)


def _run_async(coro):
    """Run an async coroutine in a new event loop (Celery worker bridge)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="process_document",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def process_document_task(self, document_id: str):
    """
    Celery task that processes a document through the full pipeline.

    Retries up to 2 times on failure with a 30-second delay.
    """
    from app.pipeline.orchestrator import process_document

    try:
        _run_async(process_document(document_id))
    except Exception as exc:
        # Retry on transient errors
        raise self.retry(exc=exc)
