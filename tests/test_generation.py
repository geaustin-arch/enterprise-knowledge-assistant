import json
import pytest
from knowledge_assistant.core import Chunk, Hit
from knowledge_assistant.model import validate_answer

HITS = [Hit(Chunk("d:0", "d", "Policy", "Manager approval required."), 1)]


@pytest.mark.parametrize(
    "ids,answer,abstained",
    [
        (["invented:0"], "Approve it.", False),
        ([], "Approve it.", False),
        (["d:0"], "Approve it. [other:0]", False),
        (["d:0"], "Cannot answer.", True),
    ],
)
def test_invalid_citations_are_rejected(ids, answer, abstained):
    with pytest.raises(ValueError):
        validate_answer(
            json.dumps({"answer": answer, "citation_ids": ids, "abstained": abstained}),
            HITS,
        )


def test_valid_citation_and_abstention():
    assert validate_answer(
        json.dumps(
            {
                "answer": "Manager approval [d:0]",
                "citation_ids": ["d:0"],
                "abstained": False,
            }
        ),
        HITS,
    ).citation_ids == ["d:0"]
    assert validate_answer(
        json.dumps(
            {"answer": "Insufficient evidence", "citation_ids": [], "abstained": True}
        ),
        HITS,
    ).abstained
