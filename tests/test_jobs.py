"""Job queue, daily limit and feedback endpoints (model replaced by fakes)."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import config, llm, main, service

client = TestClient(main.app)

FAKE_SUMMARY = "# Topic\n## Overview\nx\n## Key Points\n### A\n- p\n## Important Terms\n- **T**: d"


def submit(path, **form):
    with open(path, "rb") as f:
        return client.post("/jobs", files={"file": (path.name, f.read())}, data=form)


def wait_for(job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture
def fake_pipeline(monkeypatch):
    seen = {}

    def fake(path, mode, progress=None):
        seen["path"] = Path(path)
        seen["existed"] = Path(path).exists()
        progress("Summarizing part 1/2…")
        return FAKE_SUMMARY

    monkeypatch.setattr(service, "summarize_file", fake)
    return seen


def test_job_lifecycle_returns_pdf(sample_pdf, fake_pipeline):
    response = submit(sample_pdf, mode="short")
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    status = wait_for(job_id)
    assert status["status"] == "done"
    assert status["cached"] is False
    assert len(status["summary_id"]) == 64

    pdf = client.get(f"/jobs/{job_id}/pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["x-summary-id"] == status["summary_id"]
    # The upload existed while processing and is deleted afterwards.
    assert fake_pipeline["existed"] and not fake_pipeline["path"].exists()


def test_job_failure_is_reported(sample_pdf, monkeypatch):
    def fail(path, mode, progress=None):
        raise llm.LLMError("model down")

    monkeypatch.setattr(service, "summarize_file", fail)
    status = wait_for(submit(sample_pdf).json()["job_id"])
    assert status["status"] == "error"
    assert "unavailable right now: model down" in status["error"]


def test_pdf_before_done_and_unknown_jobs(sample_pdf, monkeypatch):
    release = []

    def slow(path, mode, progress=None):
        while not release:
            time.sleep(0.01)
        return FAKE_SUMMARY

    monkeypatch.setattr(service, "summarize_file", slow)
    job_id = submit(sample_pdf).json()["job_id"]
    assert client.get(f"/jobs/{job_id}/pdf").status_code == 409
    release.append(True)
    wait_for(job_id)
    assert client.get("/jobs/does-not-exist").status_code == 404


def test_daily_limit_counts_only_new_summaries(sample_pdf, sample_pptx, fake_pipeline, monkeypatch):
    monkeypatch.setattr(config, "DAILY_LIMIT", 1)
    wait_for(submit(sample_pdf).json()["job_id"])  # 1st new summary: allowed

    # The same deck again is cached, so it's free.
    assert submit(sample_pdf).status_code == 202
    # A second *new* deck is over the limit.
    blocked = submit(sample_pptx)
    assert blocked.status_code == 429
    assert "today's limit of 1" in blocked.json()["detail"]


def test_client_id_uses_proxy_header_only_when_trusted(monkeypatch):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"1.2.3.4, 10.0.0.1")],
        "client": ("10.0.0.1", 5000),
    }
    request = Request(scope)
    assert main._client_id(request) == "10.0.0.1"
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
    assert main._client_id(request) == "1.2.3.4"


def test_feedback_is_stored_and_counted():
    summary_id = "a" * 64
    for rating in ("up", "up", "down"):
        assert (
            client.post("/feedback", json={"summary_id": summary_id, "rating": rating}).status_code
            == 204
        )
    assert client.get("/feedback/stats").json() == {"up": 2, "down": 1, "positive_%": 67}
    assert Path(config.FEEDBACK_FILE).read_text().count(summary_id) == 3


def test_feedback_validation():
    assert client.post("/feedback", json={"summary_id": "x", "rating": "up"}).status_code == 422
    bad_rating = {"summary_id": "a" * 64, "rating": "meh"}
    assert client.post("/feedback", json=bad_rating).status_code == 422
