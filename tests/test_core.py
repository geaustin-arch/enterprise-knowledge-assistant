from knowledge_assistant.core import (
    BM25Index,
    Chunk,
    chunk_document,
    answer_with_evidence,
)


def test_chunking_overlaps_and_bounds():
    chunks = chunk_document(
        "p1", "Policy", "one two three four five six", size=4, overlap=1
    )
    assert [c.text for c in chunks] == ["one two three four", "four five six"]


def test_retrieval_ranks_relevant_and_cites():
    docs = [
        Chunk(
            "a:0",
            "a",
            "Access policy",
            "employees request access through manager approval",
        ),
        Chunk("b:0", "b", "Travel policy", "receipts are required for travel expenses"),
    ]
    hits = BM25Index(docs).search("manager approval access", 2)
    assert hits[0].chunk.document_id == "a"
    response = answer_with_evidence("who approves access?", hits)
    assert response["citations"][0]["chunk_id"] == "a:0"


def test_no_match_refuses_to_invent():
    result = answer_with_evidence("unknown", [])
    assert result["citations"] == []
    assert "could not find" in result["answer"]


def test_index_rejects_invalid_scoring_parameters_and_top_k():
    import pytest

    with pytest.raises(ValueError):
        BM25Index([], k1=0)
    with pytest.raises(ValueError):
        BM25Index([], b=1.1)
    with pytest.raises(ValueError):
        BM25Index([]).search("query", 0)


def test_api_ingestion_query_duplicates_and_invalid_input(monkeypatch, headers):
    from fastapi.testclient import TestClient
    from knowledge_assistant import app as app_module

    client = TestClient(app_module.app, headers=headers())
    doc = {
        "document_id": "policy-1",
        "title": "Access policy",
        "text": "Employees request access through manager approval.",
    }
    first = client.post("/documents", json=doc)
    assert first.status_code == 201
    assert first.json()["chunks_created"] == 1
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/documents", json=doc).status_code == 409
    result = client.post("/query", json={"question": "Who approves access?"})
    assert result.status_code == 200
    assert result.json()["citations"][0]["document_id"] == "policy-1"
    assert (
        client.post(
            "/documents", json={**doc, "document_id": "blank", "text": "   "}
        ).status_code
        == 422
    )
    assert (
        client.post("/query", json={"question": "x", "top_k": 100}).status_code == 422
    )
