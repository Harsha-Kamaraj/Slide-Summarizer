"""Extras generation with the model replaced by a fake."""

from backend import extras

SUMMARY = "# Find-S\n## Overview\nx\n## Key Points\n### A\n- p\n## Important Terms\n- **X**: y"


def test_drops_not_covered_items_and_keeps_the_rest(monkeypatch):
    reply = (
        "## Flashcards\n- Q: What does Find-S ignore? | A: Negative examples.\n"
        "- Q: What is decision making? | A: not covered in slides"
    )
    monkeypatch.setattr(extras.llm, "chat", lambda messages, **kw: reply)
    section = extras.generate_extra("flashcards", SUMMARY, "short")
    assert "Negative examples." in section
    assert "not covered" not in section


def test_unusable_reply_gives_empty_section(monkeypatch):
    monkeypatch.setattr(extras.llm, "chat", lambda messages, **kw: "Sorry, I can't help.")
    assert extras.generate_extra("exam_questions", SUMMARY, "short") == ""


def test_prompt_uses_summary_title_and_mode_count(monkeypatch):
    seen = []
    monkeypatch.setattr(
        extras.llm, "chat", lambda messages, **kw: seen.append(messages[-1]["content"]) or ""
    )
    extras.generate_extra("exam_questions", SUMMARY, "detailed")
    assert 'titled "Find-S"' in seen[0]
    assert "Write 8 likely exam questions" in seen[0]
