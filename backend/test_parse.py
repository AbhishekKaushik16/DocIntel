import asyncio
from app.pipeline.parser import parse_document
async def test():
    file_path = "uploads/99e91540-40f3-496f-b991-54a518df6efa.pdf"
    res = await parse_document(file_path)
    print(res.text)
asyncio.run(test())
