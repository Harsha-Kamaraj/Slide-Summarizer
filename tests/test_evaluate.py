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
    assert "ignores negatives" in calls[0]  # the judge sees the relevant slide


def test_judge_sees_only_slides_relevant_to_the_claims(monkeypatch):
    monkeypatch.setattr(evaluate, "SLIDES_PER_CLAIM", 1)
    deck = Deck(
        title="T",
        slides=[
            Slide(number=1, title="Find-S", text="Find-S ignores negative examples"),
            Slide(number=2, title="Version Space", text="hypotheses consistent with training data"),
            Slide(number=3, title="Decision Trees", text="split on information gain"),
        ],
    )
    picked = evaluate.relevant_slides(deck, ["The version space holds consistent hypotheses."])
    assert [s.number for s in picked] == [2]


def test_fragments_are_not_claims():
    summary = SUMMARY.replace(
        "- Find-S ignores negative examples.",
        "- Find-S ignores negative examples.\n- Steps:\nFormula: \n- 4. sunny",
    )
    claims = evaluate.extract_claims(summary)
    assert "Steps:" not in claims and "Formula: " not in claims
    assert all("sunny" not in c or "{" in c for c in claims)


def test_honest_placeholders_are_not_scored():
    summary = SUMMARY.replace(
        "- Find-S ignores negative examples.",
        "- Find-S ignores negative examples.\n- Solution not given in slides.",
    )
    assert not any("not given" in c for c in evaluate.extract_claims(summary))


def test_coverage_verdicts_per_slide(monkeypatch):
    seen = []

    def fake_chat(messages, **kwargs):
        seen.append(messages[0]["content"])
        return "1 | ADMIN\n2 | COVERED\n3 | missing\nnoise"

    monkeypatch.setattr(evaluate.llm, "chat", fake_chat)
    deck = Deck(
        title="T",
        slides=[Slide(number=n, title=f"S{n}", text="x") for n in (1, 2, 3, 4)],
    )
    verdicts = evaluate.judge_coverage(deck, "# Summary text")
    assert verdicts == {1: "ADMIN", 2: "COVERED", 3: "MISSING", 4: "UNKNOWN"}
    assert "# Summary text" in seen[0]  # the judge sees the summary
