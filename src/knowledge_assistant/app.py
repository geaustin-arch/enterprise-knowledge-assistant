from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from .core import BM25Index, chunk_document, answer_with_evidence
from .model import VertexGeminiAnswerer
import os

app = FastAPI(title="Enterprise Knowledge Assistant", version="0.1.0")
_chunks = []
_index = BM25Index(_chunks)

class DocumentIn(BaseModel):
    document_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=100_000)

class QueryIn(BaseModel):
    question: str = Field(min_length=3, max_length=2_000)
    top_k: int = Field(default=4, ge=1, le=10)

@app.get("/health")
def health():
    return {"status": "ok", "indexed_chunks": len(_chunks)}

@app.post("/documents", status_code=201)
def ingest(body: DocumentIn):
    global _chunks, _index
    if any(c.document_id == body.document_id for c in _chunks):
        raise HTTPException(409, "document_id already exists")
    _chunks.extend(chunk_document(body.document_id, body.title, body.text))
    _index = BM25Index(_chunks)
    return {"document_id": body.document_id, "chunks_created": sum(c.document_id == body.document_id for c in _chunks)}

@app.post("/query")
def query(body: QueryIn):
    hits = _index.search(body.question, body.top_k)
    result = answer_with_evidence(body.question, hits)
    if hits and os.getenv("GEMINI_ENABLED", "false").lower() == "true":
        try:
            result["answer"] = VertexGeminiAnswerer().answer(body.question, hits)
            result["generation"] = "vertex-gemini"
        except Exception as exc:
            raise HTTPException(503, f"Grounded generation unavailable: {type(exc).__name__}") from exc
    else:
        result["generation"] = "extractive-demo"
    return {"question": body.question, **result}
