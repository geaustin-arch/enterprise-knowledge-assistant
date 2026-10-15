from concurrent.futures import ThreadPoolExecutor
import os
import time
import pytest
from fastapi.testclient import TestClient
from knowledge_assistant.app import app
from knowledge_assistant.store import DocumentStore, Conflict

DOC = {
    "document_id": "policy",
    "title": "Access",
    "text": "manager approval access CANARY-PRIVATE-TEXT",
}


def test_tenant_and_reader_authorization_precede_retrieval(headers):
    a = TestClient(app, headers=headers())
    b = TestClient(app, headers=headers(tenant="tenant-b"))
    assert a.post("/documents", json=DOC).status_code == 201
    assert (
        b.post("/query", json={"question": "manager access"}).json()["citations"] == []
    )
    assert (
        b.post(
            "/documents", json={**DOC, "text": "manager access is restricted elsewhere"}
        ).status_code
        == 201
    )
    assert "CANARY" not in b.post("/query", json={"question": "manager access"}).text
    a.post(
        "/documents", json={**DOC, "document_id": "restricted", "readers": ["carol"]}
    )
    bob = TestClient(app, headers=headers(subject="bob"))
    result = bob.post("/query", json={"question": "manager access"}).json()
    assert {c["document_id"] for c in result["citations"]} == {"policy"}
    carol = TestClient(app, headers=headers(subject="carol"))
    result = carol.post("/query", json={"question": "manager access"}).json()
    assert {c["document_id"] for c in result["citations"]} == {"policy", "restricted"}
    assert bob.delete("/documents/restricted").status_code == 404


def test_update_delete_and_stale_versions(headers):
    c = TestClient(app, headers=headers())
    assert c.post("/documents", json=DOC).json()["version"] == 1
    result = c.put(
        "/documents/policy?expected_version=1",
        json={**DOC, "text": "new policy requires director approval"},
    )
    assert result.json()["version"] == 2
    assert c.put("/documents/policy?expected_version=1", json=DOC).status_code == 409
    assert (
        c.post("/query", json={"question": "CANARY PRIVATE TEXT"}).json()["citations"]
        == []
    )
    assert c.delete("/documents/policy").status_code == 204
    assert (
        c.post("/query", json={"question": "director approval"}).json()["citations"]
        == []
    )


def test_restart_encryption_expiry_and_quota():
    directory, key = os.environ["KNOWLEDGE_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"]
    body = {**DOC, "readers": [], "retention_days": 1}
    store = DocumentStore(directory, key)
    store.put("tenant-canary", "alice", body)
    restarted = DocumentStore(directory, key)
    assert restarted.chunks("tenant-canary", "alice")
    assert b"CANARY-PRIVATE-TEXT" not in restarted.path.read_bytes()
    assert b"tenant-canary" not in restarted.path.read_bytes()
    assert restarted.purge_expired(time.time() + 2 * 86400) == 1
    assert restarted.chunks("tenant-canary", "alice") == []


def test_concurrent_ingestion_has_no_lost_updates():
    directory, key = os.environ["KNOWLEDGE_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"]

    def put(index):
        return DocumentStore(directory, key).put(
            "t",
            "alice",
            {**DOC, "document_id": str(index), "readers": [], "retention_days": 30},
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(put, range(8))) == [1] * 8
    assert len(DocumentStore(directory, key).chunks("t", "alice")) == 8


def test_unauthorized_evidence_never_reaches_model(headers, monkeypatch):
    from knowledge_assistant.model import VertexGeminiAnswerer

    monkeypatch.setenv("GEMINI_ENABLED", "true")
    calls = []

    def answer(self, question, hits):
        calls.append([h.chunk.document_id for h in hits])
        raise TimeoutError()

    monkeypatch.setattr(VertexGeminiAnswerer, "answer", answer)
    a = TestClient(app, headers=headers())
    a.post("/documents", json={**DOC, "readers": ["alice"]})
    b = TestClient(app, headers=headers(subject="bob"))
    assert (
        b.post("/query", json={"question": "manager approval"}).json()["citations"]
        == []
    )
    assert calls == []
    assert (
        a.post("/query", json={"question": "manager approval"}).json()["generation"]
        == "extractive-fallback"
    )
    assert calls == [["policy"]]


def test_document_and_byte_quotas_are_enforced():
    from knowledge_assistant.store import QuotaExceeded

    directory, key = os.environ["KNOWLEDGE_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"]
    store = DocumentStore(directory, key)
    for index in range(100):
        store.put(
            "t",
            "alice",
            {**DOC, "document_id": str(index), "readers": [], "retention_days": 30},
        )
    with pytest.raises(QuotaExceeded):
        store.put(
            "t",
            "alice",
            {**DOC, "document_id": "overflow", "readers": [], "retention_days": 30},
        )
    for index in range(10):
        store.put(
            "other",
            "alice",
            {
                **DOC,
                "document_id": str(index),
                "text": "x" * 100_000,
                "readers": [],
                "retention_days": 30,
            },
        )
    with pytest.raises(QuotaExceeded):
        store.put(
            "other",
            "alice",
            {**DOC, "document_id": "overflow", "readers": [], "retention_days": 30},
        )


def test_expired_documents_are_invisible_before_cleanup(monkeypatch):
    import knowledge_assistant.store as module

    directory, key = os.environ["KNOWLEDGE_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"]
    store = DocumentStore(directory, key)
    store.put("t", "alice", {**DOC, "readers": [], "retention_days": 1})
    future = time.time() + 2 * 86400
    monkeypatch.setattr(module.time, "time", lambda: future)
    assert store.chunks("t", "alice") == []


def test_missing_encryption_configuration_fails_closed(headers, monkeypatch):
    monkeypatch.delenv("DATA_ENCRYPTION_KEY")
    c = TestClient(app, headers=headers())
    assert c.post("/documents", json=DOC).status_code == 503
