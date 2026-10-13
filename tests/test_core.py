from knowledge_assistant.core import BM25Index, Chunk, chunk_document, answer_with_evidence

def test_chunking_overlaps_and_bounds():
    chunks = chunk_document("p1", "Policy", "one two three four five six", size=4, overlap=1)
    assert [c.text for c in chunks] == ["one two three four", "four five six"]

def test_retrieval_ranks_relevant_and_cites():
    docs = [Chunk("a:0", "a", "Access policy", "employees request access through manager approval"),
            Chunk("b:0", "b", "Travel policy", "receipts are required for travel expenses")]
    hits = BM25Index(docs).search("manager approval access", 2)
    assert hits[0].chunk.document_id == "a"
    response = answer_with_evidence("who approves access?", hits)
    assert response["citations"][0]["chunk_id"] == "a:0"

def test_no_match_refuses_to_invent():
    result = answer_with_evidence("unknown", [])
    assert result["citations"] == []
    assert "could not find" in result["answer"]
