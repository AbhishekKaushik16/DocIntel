import asyncio
from sqlalchemy import select, update
from app.database import async_session_factory
from app.models import Document, DocumentStatus
from app.pipeline.tasks import process_document_task

async def main():
    async with async_session_factory() as db:
        result = await db.execute(select(Document.id))
        doc_ids = result.scalars().all()
        
        # Reset all document statuses to PENDING so they can be processed
        await db.execute(
            update(Document).values(status=DocumentStatus.PENDING)
        )
        await db.commit()
        
        print(f"Queueing {len(doc_ids)} documents for reprocessing...")
        for doc_id in doc_ids:
            process_document_task.delay(str(doc_id))
            
        print("All documents queued for reprocessing.")

if __name__ == "__main__":
    asyncio.run(main())
