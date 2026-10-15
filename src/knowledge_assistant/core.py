"""Small, explainable RAG core: bounded chunking, BM25 retrieval, citations."""

from __future__ import annotations
from dataclasses import dataclass
import math, re
from collections import Counter

TOKEN = re.compile(r"[a-z0-9][a-z0-9'-]*", re.I)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    title: str
    text: str


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN.findall(text)]


def chunk_document(
    document_id: str, title: str, text: str, size: int = 80, overlap: int = 16
) -> list[Chunk]:
    if size < 1 or overlap < 0 or overlap >= size:
        raise ValueError("Require size > overlap >= 0")
    words = text.split()
    result = []
    step = size - overlap
    for i, start in enumerate(range(0, len(words), step)):
        body = " ".join(words[start : start + size])
        if body:
            result.append(Chunk(f"{document_id}:{i}", document_id, title, body))
    return result


class BM25Index:
    """In-memory BM25 implementation so the demo stays provider-independent."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        if not math.isfinite(k1) or k1 <= 0:
            raise ValueError("k1 must be a finite positive number")
        if not math.isfinite(b) or not 0 <= b <= 1:
            raise ValueError("b must be between 0 and 1")
        self.chunks, self.k1, self.b = chunks, k1, b
        self.terms = [Counter(tokenize(c.text)) for c in chunks]
        self.lengths = [sum(tf.values()) for tf in self.terms]
        self.avgdl = sum(self.lengths) / max(1, len(self.lengths))
        self.df = Counter(term for tf in self.terms for term in tf)

    def search(self, query: str, k: int = 4) -> list[Hit]:
        if k < 1:
            raise ValueError("k must be at least 1")
        qterms = set(tokenize(query))
        n = len(self.chunks)
        hits = []
        for i, tf in enumerate(self.terms):
            score = 0.0
            dl = self.lengths[i]
            for term in qterms:
                freq = tf[term]
                if not freq:
                    continue
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                denom = freq + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1))
                score += idf * freq * (self.k1 + 1) / denom
            if score > 0:
                hits.append(Hit(self.chunks[i], score))
        return sorted(hits, key=lambda h: h.score, reverse=True)[:k]


def answer_with_evidence(question: str, hits: list[Hit]) -> dict:
    """Safe local fallback: return evidence excerpts instead of fabricating prose."""
    if not hits:
        return {
            "answer": "I could not find supporting material in the indexed documents.",
            "citations": [],
        }
    citations = [
        {
            "chunk_id": h.chunk.chunk_id,
            "document_id": h.chunk.document_id,
            "title": h.chunk.title,
            "excerpt": h.chunk.text[:400],
            "score": round(h.score, 4),
        }
        for h in hits
    ]
    return {
        "answer": "Here are the most relevant passages. Review the cited source text before acting.",
        "citations": citations,
    }
