"""Tests for Stage 1: Document Classifier."""

import pytest
from app.pipeline.classifier import classify_by_heuristic, classify_document


def test_classify_invoice_heuristic():
    invoice_text = """
    INVOICE #INV-2024-001
    Bill To: Acme Corp
    Date: 2024-05-15
    Due Date: 2024-06-15
    Subtotal: $1,000.00
    Tax Amount: $100.00
    Total Due: $1,100.00
    Payment Terms: Net 30
    """
    res = classify_by_heuristic(invoice_text)
    assert res.document_type == "invoice"
    assert res.confidence >= 0.5


def test_classify_retail_tax_invoice_heuristic():
    invoice_text = """
    Tax Invoice/Bill of Supply/Cash Memo
    Order Number: 402-8382572-8677918
    Invoice Number : TBJ6-171289
    Order Date: 04.08.2026
    Invoice Details : KA-TBJ6-1014-2627
    Invoice Date : 04.08.2026
    GST Registration No: 29AAPCA6346P1ZR
    PAN No: AAPCA6346P
    TOTAL: INR 287.00
    """
    res = classify_by_heuristic(invoice_text)
    assert res.document_type == "invoice"
    assert res.confidence >= 0.5


def test_classify_receipt_heuristic():
    receipt_text = """
    Target Store #1234
    123 Main St, Austin TX
    Transaction #98765
    Date: 05/12/2024
    Item 1: $12.99
    Subtotal: $12.99
    Tax: $1.04
    Total: $14.03
    Card ending in 4321
    Paid. Thank you for your purchase!
    """
    res = classify_by_heuristic(receipt_text)
    assert res.document_type == "receipt"
    assert res.confidence >= 0.5


def test_classify_contract_heuristic():
    contract_text = """
    MASTER SERVICES AGREEMENT
    This Agreement is entered into by and between Party A and Party B.
    Effective Date: January 1, 2024.
    WHEREAS, Party A agrees to perform services...
    Terms and conditions hereby execute this contract.
    Termination clause: 30 days notice.
    Governing Law: State of California.
    Signature: ______________
    """
    res = classify_by_heuristic(contract_text)
    assert res.document_type == "contract"
    assert res.confidence >= 0.5


def test_classify_resume_heuristic():
    resume_text = """
    Jane Doe
    jane.doe@example.com | (555) 123-4567 | linkedin.com/in/janedoe
    
    Professional Summary:
    Experienced Software Engineer with 6+ years of work experience...
    
    Work Experience:
    Senior Developer at Tech Co (2021 - Present)
    
    Education:
    Bachelor of Science in Computer Science, Stanford University
    
    Skills: Python, FastAPI, React, PostgreSQL
    """
    res = classify_by_heuristic(resume_text)
    assert res.document_type == "resume"
    assert res.confidence >= 0.5


def test_classify_resume_without_resume_keyword():
    resume_text = """
    Abhishek Kaushik
    Software Engineer 2
    Bengaluru, Karnataka
    abhishek@example.com

    Technical Skills
    Languages: Java, Typescript, SQL

    Work Experience
    Texas Instruments
    07/2022 - Current
    Software Engineer
    """
    res = classify_by_heuristic(resume_text)
    assert res.document_type == "resume"
    assert res.confidence >= 0.5


@pytest.mark.asyncio
async def test_classify_document_fallback():
    text = "Just some random notes from a meeting on Tuesday."
    res = await classify_document(text)
    assert isinstance(res.document_type, str)
