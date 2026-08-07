import asyncio
import httpx
import json
from app.config import settings
from app.pipeline.parser import parse_document
from app.pipeline.extractor import extract_with_gemini

async def test():
    file_path = "uploads/99e91540-40f3-496f-b991-54a518df6efa.pdf"
    res = await parse_document(file_path)
    data = await extract_with_gemini(res.text, "invoice")
    print(json.dumps(data, indent=2))

asyncio.run(test())
