import asyncio
from app.pipeline.orchestrator import process_document
import uuid

async def test():
    doc_uuid = "99e91540-40f3-496f-b991-54a518df6efa"
    await process_document(doc_uuid)

asyncio.run(test())
