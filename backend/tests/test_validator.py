"""Tests for Stage 3 & 4: Extractor & Validator."""

import json

import pytest

from app.pipeline.extractor import extract_with_llm, extract_with_regex
from app.pipeline.validator import validate_and_score


def test_regex_extractor_invoice():
    text = """
    INVOICE #INV-9988
    Date: 2024-04-10
    Due Date: 2024-05-10
    Total Amount: $1,250.00
    Subtotal: $1,150.00
    Email: billing@acme.com
    """
    data = extract_with_regex(text, "invoice")
    assert data["invoice_number"] == "INV-9988"
    assert data["invoice_date"] == "2024-04-10"
    assert data["total_amount"] == 1250.00


def test_regex_extractor_resume():
    text = """
    John Smith
    Software Engineer
    Austin, Texas
    john.smith@tech.org
    Phone: +1-555-019-2834
    LinkedIn: https://linkedin.com/in/johnsmith
    Technical Skills
    Languages: Python, SQL
    Work Experience
    Acme Corp
    01/2021 - Current
    Senior Developer
    """
    data = extract_with_regex(text, "resume")
    assert data["full_name"] == "John Smith"
    assert data["email"] == "john.smith@tech.org"
    assert data["phone"] == "+1-555-019-2834"
    assert "Python" in data["skills"]
    assert data["work_experience"][0]["company"] == "Acme Corp"
    assert data["linkedin_url"] == "https://linkedin.com/in/johnsmith"


@pytest.mark.asyncio
async def test_gemini_extractor_uses_json_generation_config(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "full_name": "John Smith",
                                            "email": "john.smith@tech.org",
                                            "phone": "+1-555-019-2834",
                                            "location": "Austin, Texas",
                                            "skills": ["Python", "SQL"],
                                            "linkedin_url": "https://linkedin.com/in/johnsmith",
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("app.pipeline.extractor.settings.llm_provider", "gemini")
    monkeypatch.setattr("app.pipeline.extractor.settings.gemini_api_key", "test-key")
    monkeypatch.setattr("app.pipeline.extractor.settings.gemini_model", "gemini-test")
    monkeypatch.setattr("app.pipeline.extractor.httpx.AsyncClient", FakeAsyncClient)

    data = await extract_with_llm("John Smith\njohn.smith@tech.org", "resume")

    assert data["full_name"] == "John Smith"
    assert captured["url"].endswith("/models/gemini-test:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "test-key"
    generation_config = captured["payload"]["generationConfig"]
    assert generation_config["responseMimeType"] == "application/json"


def test_validator_invoice_consistency():
    data = {
        "invoice_number": "INV-100",
        "invoice_date": "2024-01-01",
        "due_date": "2024-02-01",
        "vendor_name": "Acme Inc",
        "subtotal": 100.0,
        "tax_amount": 10.0,
        "total_amount": 110.0,
        "line_items": [
            {"description": "Widget A", "amount": 50.0},
            {"description": "Widget B", "amount": 60.0},
        ],
    }

    res = validate_and_score(
        extracted_data=data,
        document_type="invoice",
        extraction_method="llm",
    )

    assert res.confidence_score >= 0.7
    assert res.field_completeness > 0.6


def test_validator_invoice_mismatch_warning():
    data = {
        "invoice_number": "INV-100",
        "invoice_date": "2024-01-01",
        "vendor_name": "Acme Inc",
        "subtotal": 100.0,
        "tax_amount": 10.0,
        "total_amount": 500.0,  # Mismatch! 100+10 != 500
    }

    res = validate_and_score(
        extracted_data=data,
        document_type="invoice",
        extraction_method="llm",
    )

    warnings = [i for i in res.issues if i.field == "total_amount"]
    assert len(warnings) > 0
