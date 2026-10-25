"""API behaviour with the model replaced by a fake, so tests run in seconds."""

import pytest
from fastapi.testclient import TestClient

from backend import config, llm, main

client = TestClient(main.app)

FAKE_SUMMARY = """# Test Topic
## Overview
An overview.
## Key Points
### Idea
- A point.
## Important Terms
- **Term**: definition."""


@pytest.fixture
def fake_summarizer(monkeypatch):
    calls = {}

    def fake(deck, mode):
        calls["mode"] = mode
        calls["slides"] = len(deck.slides)
        return FAKE_SUMMARY

    monkeypatch.setattr(main, "summarize_deck", fake)
    return calls


def upload(path, **form):
    with open(path, "rb") as f:
        return client.post("/summarize", files={"file": (path.name, f.read())}, data=form)


def test_health_reports_llm_status(monkeypatch):
    monkeypatch.setattr(main, "_llm_reachable", lambda: False)
    assert client.get("/health").json() == {"status": "ok", "ollama": False}


def test_summarize_returns_pdf_download(sample_pptx, fake_summarizer):
    response = upload(sample_pptx, mode="short")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="intro_summary.pdf"' in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")
    assert fake_summarizer == {"mode": "short", "slides": 3}


def test_default_mode_is_detailed(sample_pdf, fake_summarizer):
    upload(sample_pdf)
    assert fake_summarizer["mode"] == "detailed"


def test_rejects_unsupported_type(tmp_path, fake_summarizer):
    path = tmp_path / "notes.docx"
    path.write_bytes(b"hello")
    response = upload(path)
    assert response.status_code == 415
    assert "Unsupported file type" in response.json()["detail"]


def test_rejects_invalid_mode(sample_pdf, fake_summarizer):
    assert upload(sample_pdf, mode="essay").status_code == 422


def test_rejects_oversized_file(sample_pdf, fake_summarizer, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)
    response = upload(sample_pdf)
    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


def test_corrupt_file_gives_clear_error(tmp_path, fake_summarizer):
    path = tmp_path / "broken.pptx"
    path.write_bytes(b"not a pptx")
    response = upload(path)
    assert response.status_code == 422
    # Names the student's file, not the server's temp file.
    assert response.json()["detail"] == "Could not open 'broken.pptx': it may be corrupt."


def test_llm_down_gives_503_with_reason(sample_pdf, monkeypatch):
    def fail(deck, mode):
        raise llm.LLMError("Can't reach the LLM server. Start it with 'ollama serve'.")

    monkeypatch.setattr(main, "summarize_deck", fail)
    response = upload(sample_pdf)
    assert response.status_code == 503
    assert "ollama serve" in response.json()["detail"]


def test_uploaded_file_is_not_kept(sample_pdf, fake_summarizer, monkeypatch):
    seen = []
    real_extract = main.extract_deck

    def spy(path):
        seen.append(path)
        return real_extract(path)

    monkeypatch.setattr(main, "extract_deck", spy)
    upload(sample_pdf)

    from pathlib import Path

    assert seen and not Path(seen[0]).exists()  # temp file deleted after the request
