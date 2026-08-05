"""API integration tests for document endpoints."""

import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


@pytest.mark.asyncio
async def test_upload_and_list_documents(client, db_session, monkeypatch):
    events = []
    original_commit = db_session.commit

    async def tracking_commit():
        events.append("commit")
        await original_commit()

    def fake_delay(_document_id):
        events.append("delay")

    db_session.commit = tracking_commit
    monkeypatch.setattr("app.pipeline.tasks.process_document_task.delay", fake_delay)

    # Upload a sample text file
    file_content = b"INVOICE #123\nDate: 2024-01-01\nTotal: $500.00\nVendor: Global Corp"
    files = {"files": ("invoice_sample.txt", file_content, "text/plain")}

    response = await client.post("/api/documents/upload", files=files)
    assert response.status_code == 201
    res_json = response.json()
    assert len(res_json) == 1
    doc_id = res_json[0]["id"]
    assert res_json[0]["status"] == "pending"
    assert events[:2] == ["commit", "delay"]

    # List documents
    list_res = await client.get("/api/documents")
    assert list_res.status_code == 200
    list_json = list_res.json()
    assert list_json["total"] == 1
    assert list_json["documents"][0]["id"] == doc_id

    # Get single document detail
    detail_res = await client.get(f"/api/documents/{doc_id}")
    assert detail_res.status_code == 200
    detail_json = detail_res.json()
    assert detail_json["id"] == doc_id
    assert detail_json["original_filename"] == "invoice_sample.txt"


@pytest.mark.asyncio
async def test_dashboard_stats(client):
    response = await client.get("/api/documents/stats/dashboard")
    assert response.status_code == 200
    stats = response.json()
    assert "total_documents" in stats
    assert "completed" in stats
    assert "needs_review" in stats
