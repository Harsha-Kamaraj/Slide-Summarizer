"""Recovery and quota regression tests use no real AI or remote accounts."""

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from backend import cache, config, execution, jobs, llm, pacing, service


def test_requests_share_a_token_window_but_models_have_separate_budgets(monkeypatch):
    now = [0.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(config, "LLM_TOKENS_PER_MINUTE", 100)
    monkeypatch.setattr(config, "LLM_REQUESTS_PER_MINUTE", 10)
    limiter = pacing.Limiter(clock=lambda: now[0], sleep=sleep)
    limiter.acquire(("provider", "model-a"), 70)
    limiter.acquire(("provider", "model-b"), 70)
    assert not waits
    limiter.acquire(("provider", "model-a"), 40)
    assert sum(waits) == 60
    limiter.defer(("provider", "model-a"), 7)
    limiter.acquire(("provider", "model-a"), 10)
    assert sum(waits) == 67


def test_request_budget_rejects_before_contacting_provider(monkeypatch):
    monkeypatch.setattr(config, "LLM_INPUT_TOKEN_BUDGET", 100)
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: pytest.fail("Oversized request sent"))
    with pytest.raises(llm.RequestTooLarge):
        llm.chat([{"role": "user", "content": "very long text " * 100}])


def test_context_and_deadline_reach_pool_workers():
    messages = []
    with execution.scope(messages.append, 10):
        with ThreadPoolExecutor(max_workers=2) as pool:
            execution.parallel_map(pool, lambda n: execution.report(str(n)), [1, 2])
    assert sorted(messages) == ["1", "2"]
    with execution.scope(messages.append, -1):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(execution.DeadlineExceeded):
                execution.parallel_map(pool, lambda _: execution.remaining(), [1])


def test_successful_ai_reply_is_reused_after_local_disk_loss(monkeypatch):
    remote, calls = {}, []
    monkeypatch.setattr(config, "CACHE_HF_REPO", "test/cache")
    monkeypatch.setattr(cache, "_remote_put", lambda key, text: remote.update({key: text}))
    monkeypatch.setattr(cache, "_remote_get", remote.get)

    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Saved notes"}}]})

    monkeypatch.setattr(llm.httpx, "post", post)
    message = [{"role": "user", "content": "Summarize this section"}]
    assert llm.chat(message) == "Saved notes"
    shutil.rmtree(config.CACHE_DIR)
    assert llm.chat(message) == "Saved notes"
    assert len(calls) == 1


def test_truncated_ai_output_is_not_cached(monkeypatch):
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}
        ),
    )
    with pytest.raises(llm.RequestTooLarge):
        llm.chat([{"role": "user", "content": "topic"}])
    assert not list(Path(config.CACHE_DIR).glob("*.md"))


def test_completed_and_interrupted_jobs_are_distinguished_after_restart(monkeypatch):
    monkeypatch.setattr(jobs, "_jobs", {})
    completed = jobs.Job(
        id="a" * 32, filename="deck.pdf", status="done", markdown="# Saved", finished=time.time()
    )
    unfinished = jobs.Job(id="b" * 32, filename="deck.pdf", status="running")
    jobs._save(completed)
    jobs._save(unfinished)
    assert jobs.get(completed.id).markdown == "# Saved"
    recovered = jobs.get(unfinished.id)
    assert recovered.status == "error"
    assert "reuse saved sections" in recovered.error


def test_extras_are_reused_individually_and_summary_changes_invalidate_them(
    sample_pdf, monkeypatch
):
    calls = []
    monkeypatch.setattr(service, "summarize_file", lambda *a, **k: "# Summary")
    monkeypatch.setattr(service, "generate_extra", lambda extra, *a: calls.append(extra) or extra)
    service.run(sample_pdf, "file", "short", ["exam_questions"])
    service.run(sample_pdf, "file", "short", ["exam_questions", "flashcards"])
    _, hit = service.run(sample_pdf, "file", "short", ["exam_questions", "flashcards"])
    assert hit and calls == ["exam_questions", "flashcards"]
    before = cache.cache_key("file", "short", "flashcards")
    monkeypatch.setattr(config, "NOTES_MODEL", "new-notes-model")
    assert before != cache.cache_key("file", "short", "flashcards")


def test_same_model_fallback_does_not_double_the_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "FALLBACK_BASE_URL", config.LLM_BASE_URL)
    monkeypatch.setattr(config, "FALLBACK_VISION_MODEL", config.VISION_MODEL)
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 0)
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: calls.append(1) or httpx.Response(429))
    with pytest.raises(llm.ProviderUnavailable):
        llm.chat(
            [{"role": "user", "content": [{"type": "text", "text": "diagram"}]}],
            model=config.VISION_MODEL,
        )
    assert calls == [1]


def test_long_provider_cooldown_fails_promptly_without_sleep(monkeypatch):
    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: httpx.Response(429, headers={"retry-after": "3600"})
    )
    monkeypatch.setattr(llm, "_sleep", lambda _: pytest.fail("Should not wait an hour"))
    with pytest.raises(llm.ProviderUnavailable, match="try again later"):
        llm.chat([{"role": "user", "content": "test"}])


def test_hosted_retry_cap_overrides_legacy_dashboard_setting(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "LLM_TOKENS_PER_MINUTE", 7200)
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 6)
    monkeypatch.setattr(config, "HOSTED_MAX_RETRIES", 2)
    monkeypatch.setattr(pacing.limiter, "acquire", lambda *a: None)
    monkeypatch.setattr(llm, "_sleep", lambda _: None)
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: calls.append(1) or httpx.Response(503))
    with pytest.raises(llm.ProviderUnavailable):
        llm.chat([{"role": "user", "content": "test retry cap"}])
    assert len(calls) == 3
