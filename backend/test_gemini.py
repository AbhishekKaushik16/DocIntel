import asyncio
import httpx
import json
import os
from app.config import settings

async def test():
    text = """
Tax Invoice/Bill of Supply/Cash Memo
Order Number: 402-8382572-8677918
Invoice Number : TBJ6-171289
Order Date: 04.08.2026
Invoice Date : 04.08.2026

1 Amul A+ Cheese Slices, 200g
₹114.00

2 Fresh Cauliflower small 1 PC 
₹23.00

TOTAL: ₹287.00
Sold By: Amazon Retail India Private Limited 
Billing Address : Abhishek
"""
    prompt = f"""You are an expert document data extractor. Extract ALL relevant structured data from this 'invoice' document into a comprehensive JSON object.

Rules:
1. Use snake_case for all JSON field names.
2. Extract as many relevant fields as possible: dates, amounts, names, addresses, line items, taxes, totals, status, terms, identifiers, and any key entities.
3. Preserve nested lists or structures (e.g. line_items, items, work_experience) where appropriate.
4. If a field cannot be determined, do not invent it.
5. Be precise and extract only facts explicitly stated in the document.

Return ONLY a single valid JSON object. Do not include markdown code block syntax unless necessary.
Document text:
{text}
"""
    model_name = settings.fast_model_name if settings.fast_model_name.startswith("models/") else f"models/{settings.fast_model_name}"
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent"
    payload = {
        "systemInstruction": {
            "parts": [{"text": "You are a professional document extractor. Output a comprehensive and valid JSON object."}]
        },
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0},
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key}, json=payload)
    print(resp.json())

asyncio.run(test())
