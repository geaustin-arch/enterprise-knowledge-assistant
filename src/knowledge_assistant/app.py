from cryptography.fernet import InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .core import BM25Index, chunk_document, answer_with_evidence
from .model import VertexGeminiAnswerer
from .security import Principal, require, secure_app
from .store import DocumentStore, Conflict, NotFound, QuotaExceeded
import os
import sqlite3

app = FastAPI(
    title="Enterprise Knowledge Assistant",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
secure_app(app)


class DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$", max_length=120
    )
    title: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=100_000)
    readers: list[str] = Field(default_factory=list, max_length=100)
    retention_days: int = Field(default=30, ge=1, le=365, strict=True)

    @field_validator("document_id", "title", "text")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain non-whitespace characters")
        return value

    @field_validator("readers")
    @classmethod
    def reader_ids(cls, value):
        if any(not item.strip() or len(item) > 200 for item in value):
            raise ValueError("Invalid reader")
        return list(dict.fromkeys(value))


class QueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=2_000)
    top_k: int = Field(default=4, ge=1, le=10)


def store():
    directory, key = os.getenv("KNOWLEDGE_DATA_DIR"), os.getenv("DATA_ENCRYPTION_KEY")
    if not directory or not key:
        raise HTTPException(503, "Encrypted storage is not configured")
    try:
        return DocumentStore(directory, key)
    except (ValueError, OSError):
        raise HTTPException(503, "Encrypted storage is unavailable") from None


def operation(call):
    try:
        return call()
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except NotFound:
        raise HTTPException(404, "Document not found") from None
    except QuotaExceeded:
        raise HTTPException(429, "Tenant document quota exceeded") from None
    except ValueError:
        raise HTTPException(422, "Invalid document") from None
    except (sqlite3.Error, InvalidToken, OSError):
        raise HTTPException(503, "Document storage is unavailable") from None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/documents", status_code=201)
def ingest(
    body: DocumentIn,
    principal: Principal = Depends(require("documents:write")),
    db: DocumentStore = Depends(store),
):
    version = operation(
        lambda: db.put(principal.tenant, principal.subject, body.model_dump())
    )
    return {
        "document_id": body.document_id,
        "version": version,
        "chunks_created": len(chunk_document(body.document_id, body.title, body.text)),
    }


@app.put("/documents/{document_id}")
def update(
    document_id: str,
    body: DocumentIn,
    expected_version: int = Query(ge=1),
    principal: Principal = Depends(require("documents:write")),
    db: DocumentStore = Depends(store),
):
    if document_id != body.document_id:
        raise HTTPException(422, "Document ID must match the path")
    version = operation(
        lambda: db.put(
            principal.tenant,
            principal.subject,
            body.model_dump(),
            expected_version,
            "documents:admin" in principal.permissions,
        )
    )
    return {"document_id": document_id, "version": version}


@app.delete("/documents/{document_id}", status_code=204)
def delete(
    document_id: str,
    principal: Principal = Depends(require("documents:write")),
    db: DocumentStore = Depends(store),
):
    operation(
        lambda: db.delete(
            principal.tenant,
            principal.subject,
            document_id,
            "documents:admin" in principal.permissions,
        )
    )
    return Response(status_code=204)


@app.post("/query")
def query(
    body: QueryIn,
    principal: Principal = Depends(require("knowledge:query")),
    db: DocumentStore = Depends(store),
):
    chunks = operation(lambda: db.chunks(principal.tenant, principal.subject))
    hits = BM25Index(chunks).search(body.question, body.top_k)
    result = answer_with_evidence(body.question, hits)
    result["generation"] = "extractive-demo"
    if hits and os.getenv("GEMINI_ENABLED", "false").lower() == "true":
        try:
            generated = VertexGeminiAnswerer().answer(body.question, hits)
            if not generated.abstained:
                ids = set(generated.citation_ids)
                result["answer"] = generated.answer
                result["citations"] = [
                    c for c in result["citations"] if c["chunk_id"] in ids
                ]
                result["generation"] = "vertex-gemini"
            else:
                result = {
                    "answer": "The evidence does not support an answer.",
                    "citations": [],
                    "generation": "vertex-gemini-abstention",
                }
        except Exception:
            # Preserve authorized evidence when optional generation fails; never echo provider errors.
            result["generation"] = "extractive-fallback"
    return {"question": body.question, **result}
