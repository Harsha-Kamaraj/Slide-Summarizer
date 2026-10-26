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
        return f"# T\n## Overview\nnotes #{n}\n## Key Points"

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
- point"""


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


def test_parallel_map_keeps_chunk_order_and_uses_notes_model(monkeypatch):
    import threading
    import time

    monkeypatch.setattr(config, "CHUNK_SIZE", 1)
    monkeypatch.setattr(config, "LLM_PARALLEL_CALLS", 4)
    monkeypatch.setattr(config, "NOTES_MODEL", "small-model")
    models, active, peak = [], [0], [0]
    lock = threading.Lock()

    def fake_chat(messages, model=None, **kwargs):
        content = messages[-1]["content"]
        if "revision notes written section by section" in content:  # reduce step
            # Echo the notes (minus the template's headings) so order can be checked.
            notes = content.replace("#", "")
            return "# T\n## Overview\n" + notes + "\n## Key Points"
        with lock:
            models.append(model)
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)  # overlap the calls so parallelism is observable
        with lock:
            active[0] -= 1
        return f"notes for {content.split('slides ')[1].split(' to')[0]}"

    monkeypatch.setattr(summarize.llm, "chat", fake_chat)
    result = summarize.summarize_deck(make_deck(4))

    assert peak[0] > 1  # calls really overlapped
    assert set(models) == {"small-model"}
    # Notes reach the merge in slide order even though calls finished in any order.
    assert result.index("notes for 1") < result.index("notes for 2") < result.index("notes for 4")


def test_progress_messages_follow_the_work(fake_llm, monkeypatch):
    monkeypatch.setattr(config, "CHUNK_SIZE", 2)
    messages = []
    summarize.summarize_deck(make_deck(5), progress=messages.append)
    assert messages == [
        "Summarizing part 1/3…",
        "Summarizing part 2/3…",
        "Summarizing part 3/3…",
        "Putting the summary together…",
    ]


def test_terms_come_from_grounded_chunk_definitions():
    chunks = [
        [
            Slide(
                number=1,
                title="Version Space",
                text="Version space is the set of all hypotheses consistent with the training data",
            )
        ],
        [
            Slide(
                number=2,
                title="Limitations",
                text="Find-S does not provide a backtracking technique",
            )
        ],
    ]
    notes = [
        "### Version Space\n- point\nTerms:\n- **Version space**: set of all hypotheses consistent with the training data",
        "### Limits\n- point\nTerms:\n"
        "- Backtracking: a search technique that revisits earlier decisions to explore alternatives\n"
        "- version space: duplicate from a later chunk",
    ]
    terms = summarize.collect_terms(chunks, notes, limit=15)
    # The invented definition (words not on the slides) and the duplicate are dropped.
    assert terms == [("Version space", "set of all hypotheses consistent with the training data")]


def test_terms_are_appended_after_the_summary(monkeypatch):
    replies = iter(
        [
            "### A\n- p\nTerms:\n- Fact 1: the fact from topic 1",  # chunk notes
            "# T\n## Overview\nx\n## Key Points\n### A\n- p",  # merge
        ]
    )
    monkeypatch.setattr(summarize.llm, "chat", lambda messages, **kw: next(replies))
    result = summarize.summarize_deck(make_deck(1), mode="short")
    assert result.endswith("## Important Terms\n- **Fact 1**: the fact from topic 1")


def test_oversized_notes_are_split_without_losing_source_text(monkeypatch):
    source = "\n\n".join(f"unique_fact_{i}: " + "detail " * 20 for i in range(12))
    accepted = []

    def ask(system, prompt, model=None):
        if len(prompt) > 500:
            raise summarize.llm.RequestTooLarge("too large")
        accepted.append(prompt)
        return prompt

    monkeypatch.setattr(summarize, "_ask", ask)
    updates = []
    result = summarize._notes_with_splitting("system", lambda text: text, source, updates.append)
    assert len(accepted) > 1
    for i in range(12):
        assert f"unique_fact_{i}:" in result
    assert any("smaller sections" in text for text in updates)


def test_oversized_final_merge_builds_one_document_preserving_all_parts(monkeypatch):
    notes = "\n\n".join(f"unique_fact_{i}: " + "detail " * 15 for i in range(8))

    def ask(system, prompt, model=None):
        text = prompt.split("\nNotes:\n", 1)[1]
        if len(text) > 450:
            raise summarize.llm.RequestTooLarge("too large")
        return f"# Lecture\n## Overview\nThis section.\n## Key Points\n### Topic\n- {text}"

    monkeypatch.setattr(summarize, "_ask", ask)
    result = summarize._summary_with_splitting(
        "system", "Lecture", "detailed", notes, lambda _: None
    )
    assert summarize.structure_problem(result) is None
    for i in range(8):
        assert f"unique_fact_{i}:" in result


def test_size_splitting_is_bounded_when_provider_rejects_everything(monkeypatch):
    calls = []

    def reject(*args, **kwargs):
        calls.append(1)
        raise summarize.llm.RequestTooLarge("too large")

    monkeypatch.setattr(summarize, "_ask", reject)
    with pytest.raises(summarize.llm.RequestTooLarge, match="after splitting"):
        summarize._notes_with_splitting("system", lambda text: text, "x" * 20000, lambda _: None)
    assert len(calls) <= 7
