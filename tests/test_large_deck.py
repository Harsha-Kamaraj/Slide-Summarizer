"""89-page integration test: real parsing/PDF/cache, deterministic fake AI."""

import hashlib
import re

import httpx
import pymupdf

from backend import config, llm, pacing, service
from backend.pdf_builder import build_pdf, parse_summary


def test_large_deck_is_split_before_requests_and_all_extras_are_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VISION_ENABLED", False)
    monkeypatch.setattr(config, "SLOW_TABLE_FALLBACK", False)
    monkeypatch.setattr(config, "LLM_INPUT_TOKEN_BUDGET", 2100)
    monkeypatch.setattr(config, "CHUNK_SIZE", 8)
    source = tmp_path / "89-page-test.pdf"
    doc = pymupdf.open()
    for n in range(1, 90):
        page = doc.new_page(width=720, height=480)
        page.insert_text((40, 45), f"Topic {n}", fontsize=20)
        page.insert_textbox(
            pymupdf.Rect(40, 80, 680, 440),
            f"SOURCE_{n:03d}: "
            + "This topic explains relationships between data records with a worked example. " * 8,
            fontsize=12,
        )
    doc.save(source)
    doc.close()
    sent = []

    def fake_post(*args, **kwargs):
        payload = kwargs["json"]
        sent.append(payload)
        assert pacing.estimate_tokens(payload["messages"]) <= config.LLM_INPUT_TOKEN_BUDGET
        prompt = payload["messages"][-1]["content"]
        markers = sorted(set(re.findall(r"SOURCE_\d{3}", prompt)))
        if "likely exam questions a lecturer" in prompt:
            reply = "## Likely Exam Questions\n1. What is a relationship?\n   Answer: A connection between records."
        elif "flashcards for quick revision" in prompt:
            reply = "## Flashcards\n- Q: What is a relationship? | A: A connection between records."
        else:
            lines = "\n".join(
                f"- {m}: Relationships connect data records. A worked example explains this connection."
                for m in markers
            )
            reply = (
                "# Data Relationships\n## Overview\nRelationships connect records.\n## Key Points\n### Concepts\n"
                + lines
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": reply}, "finish_reason": "stop"}]}
        )

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    updates = []
    markdown, hit = service.run(
        source, digest, "detailed", ["exam_questions", "flashcards"], updates.append
    )
    assert not hit
    for n in range(1, 90):
        assert f"SOURCE_{n:03d}" in markdown
    parsed = parse_summary(markdown)
    assert parsed.exam_questions and parsed.flashcards
    with pymupdf.open(stream=build_pdf(parsed), filetype="pdf") as output:
        text = "".join(page.get_text() for page in output)
        assert "SOURCE_089" in text and "Likely Exam Questions" in text and "Flashcards" in text
    calls = len(sent)
    again, hit = service.run(source, digest, "detailed", ["exam_questions", "flashcards"])
    assert hit and again == markdown and len(sent) == calls
