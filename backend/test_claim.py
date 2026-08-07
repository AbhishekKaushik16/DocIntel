import asyncio
from sqlalchemy import select, update
from app.database import async_session_factory
from app.models.document import Document, DocumentStatus
import uuid

async def test():
    doc_uuid = uuid.UUID("99e91540-40f3-496f-b991-54a518df6efa")
    async with async_session_factory() as db:
        res = await db.execute(select(Document).where(Document.id == doc_uuid))
        doc = res.scalar_one_or_none()
        print(f"Current status before claim: {doc.status}")

        claim_result = await db.execute(
            update(Document)
            .where(Document.id == doc_uuid)
            .where(Document.status.in_([
                DocumentStatus.PENDING,
                DocumentStatus.FAILED,
                DocumentStatus.NEEDS_REVIEW,
            ]))
            .values(status=DocumentStatus.PROCESSING)
            .returning(Document)
        )
        await db.commit()
        claimed = claim_result.scalar_one_or_none()
        print(f"Claimed document: {claimed}")
asyncio.run(test())
