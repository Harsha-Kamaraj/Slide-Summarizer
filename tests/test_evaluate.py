"""Evaluation helpers: claim extraction and judge-reply parsing."""

from backend.extract import Deck, Slide
from eval import evaluate

SUMMARY = """# T
## Overview
Overview text.
## Key Points
### Find-S
- Find-S ignores negative examples.
Formula: h = {sunny, warm, ?, strong, ?, same}
1. Initialise h to the most specific hypothesis.
## Important Terms
- **Version Space**: hypotheses consistent with the data.
"""


def test_claims_cover_bullets_formulas_steps_and_terms():
    assert evaluate.extract_claims(SUMMARY) == [
        "Find-S ignores negative examples.",
        "Formula: h = {sunny, warm, ?, strong, ?, same}",
        "1. Initialise h to the most specific hypothesis.",
        "Version Space: hypotheses consistent with the data.",
    ]


def test_parse_verdicts_tolerates_noise_and_missing_lines():
    reply = "Here you go:\n1 | SUPPORTED | 3\n  3 | partial | -\n2 | UNSUPPORTED | -\nextra text"
    assert evaluate.parse_verdicts(reply, 4) == ["SUPPORTED", "UNSUPPORTED", "PARTIAL", "UNKNOWN"]


def test_judge_batches_claims(monkeypatch):
    calls = []

    def fake_chat(messages, **kwargs):
        content = messages[0]["content"]
        calls.append(content)
        n = content.split("Claims:")[1].count("\n") - 1
        return "\n".join(f"{i} | SUPPORTED | 1" for i in range(1, n + 2))

    monkeypatch.setattr(evaluate.llm, "chat", fake_chat)
    monkeypatch.setattr(evaluate, "CLAIMS_PER_CALL", 2)
    deck = Deck(title="T", slides=[Slide(number=1, title="Find-S", text="ignores negatives")])

    verdicts = evaluate.judge_claims(deck, ["a", "b", "c"])

    assert len(calls) == 2  # 3 claims, 2 per call
    assert verdicts == ["SUPPORTED"] * 3
    assert "ignores negatives" in calls[0]  # the judge sees the slides
