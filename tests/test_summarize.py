"""Map-reduce flow, with the LLM replaced by a fake that records its prompts."""

import pytest

from backend import config, summarize
from backend.extract import Deck, ExtractionError, Slide


@pytest.fixture
def fake_llm(monkeypatch):
    prompts = []

    def fake_chat(messages, **kwargs):
        prompts.append(messages[-1]["content"])
        # Well-formed reply so the structure check doesn't trigger a fix-up call.
        n = len(prompts)
        return f"# T\n## Overview\nnotes #{n}\n## Key Points\n## Important Terms"

    monkeypatch.setattr(summarize.llm, "chat", fake_chat)
    return prompts


def make_deck(n: int) -> Deck:
    slides = [Slide(number=i, title=f"Topic {i}", text=f"fact {i}") for i in range(1, n + 1)]
    return Deck(title="Test Deck", slides=slides)


def test_one_map_call_per_chunk_then_one_merge(fake_llm, monkeypatch):
    monkeypatch.setattr(config, "CHUNK_SIZE", 4)
    summarize.summarize_deck(make_deck(10))

    # 10 slides / 4 per chunk = 3 map calls + 1 reduce call
    assert len(fake_llm) == 4
    assert "slides 1 to 4" in fake_llm[0]
    assert "slides 9 to 10" in fake_llm[2]
    assert "fact 9" in fake_llm[2]
    # The reduce prompt sees every chunk's notes, not the raw slides.
    assert all(f"notes #{i}" in fake_llm[3] for i in (1, 2, 3))
    assert "fact 1" not in fake_llm[3]


def test_mode_reaches_both_map_and_reduce_prompts(fake_llm):
    # The map step must know the mode too: the merge can't add detail the
    # chunk notes already dropped.
    summarize.summarize_deck(make_deck(2), mode="short")
    assert summarize.NOTES_INSTRUCTIONS["short"] in fake_llm[0]
    assert summarize.LENGTH_INSTRUCTIONS["short"] in fake_llm[-1]


def test_skips_empty_slides_and_fails_if_nothing_left(fake_llm):
    deck = Deck(title="Scanned", slides=[Slide(number=1, title="", text="")])
    with pytest.raises(ExtractionError, match="No text found"):
        summarize.summarize_deck(deck)
    assert fake_llm == []


def test_format_slides_includes_notes():
    text = summarize.format_slides([Slide(number=3, title="", text="body", notes="say this")])
    assert "--- Slide 3: (untitled) ---" in text
    assert "Speaker notes: say this" in text


def test_prompts_with_braces_do_not_break_templates(fake_llm):
    # Lecture content often has code or set notation with literal braces.
    deck = Deck(title="Sets", slides=[Slide(number=1, title="A = {1, 2}", text="f(x) {}")])
    summarize.summarize_deck(deck)
    assert "A = {1, 2}" in fake_llm[0]


GOOD_SUMMARY = """# Topic
## Overview
text
## Key Points
### A
- point
## Important Terms
- **X**: y"""


def test_structure_check_accepts_valid_summary():
    assert summarize.structure_problem(GOOD_SUMMARY) is None


def test_structure_check_catches_repeated_sections():
    bad = GOOD_SUMMARY + "\n## Find-S\n## Overview\nmore"
    assert "expected sections" in summarize.structure_problem(bad)


def test_malformed_summary_gets_one_fix_attempt(monkeypatch):
    replies = iter(["notes", "# T\n## Overview\n## Overview", GOOD_SUMMARY])
    calls = []

    def fake_chat(messages, **kwargs):
        calls.append(messages)
        return next(replies)

    monkeypatch.setattr(summarize.llm, "chat", fake_chat)
    result = summarize.summarize_deck(make_deck(2))

    assert result == GOOD_SUMMARY
    assert len(calls) == 3  # map, reduce, fix
    assert "did not follow the required structure" in calls[2][-1]["content"]
