"""llm.chat error handling, using httpx's mock transport instead of a real server."""

import httpx
import pytest

from backend import llm


@pytest.fixture
def fake_server(monkeypatch):
    """Route llm.chat's HTTP calls to a handler function the test provides."""

    def install(handler):
        def fake_post(url, **kwargs):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                return client.post(url, **kwargs)

        monkeypatch.setattr(llm.httpx, "post", fake_post)

    return install


def ok_reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def test_returns_reply_and_sends_openai_format(fake_server):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = request.read()
        return ok_reply("  hello  ")

    fake_server(handler)
    assert llm.chat([{"role": "user", "content": "hi"}], model="m") == "hello"
    assert seen["path"].endswith("/chat/completions")
    assert b'"model":"m"' in seen["body"].replace(b" ", b"")


def test_missing_model_suggests_ollama_pull(fake_server):
    fake_server(lambda request: httpx.Response(404, json={"error": "not found"}))
    with pytest.raises(llm.LLMError, match="ollama pull gemma-x"):
        llm.chat([{"role": "user", "content": "hi"}], model="gemma-x")


def test_server_not_running(fake_server):
    def handler(request):
        raise httpx.ConnectError("refused")

    fake_server(handler)
    with pytest.raises(llm.LLMError, match="ollama serve"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_retries_after_timeout(fake_server):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        return ok_reply("done")

    fake_server(handler)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "done"
    assert len(calls) == 2


def test_rate_limit_waits_for_retry_after(fake_server, monkeypatch):
    waits, calls = [], []
    monkeypatch.setattr(llm, "_sleep", waits.append)

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return ok_reply("done")

    fake_server(handler)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "done"
    assert waits == [7]


@pytest.fixture
def with_fallback(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_BASE_URL", "http://primary/v1")
    monkeypatch.setattr(llm.config, "FALLBACK_BASE_URL", "http://fallback/v1")
    monkeypatch.setattr(llm.config, "FALLBACK_MODEL", "backup-text")
    monkeypatch.setattr(llm.config, "FALLBACK_VISION_MODEL", "")
    monkeypatch.setattr(llm, "_sleep", lambda s: None)


def test_falls_back_when_primary_is_rate_limited(fake_server, with_fallback):
    seen = []

    def handler(request):
        seen.append((request.url.host, request.read()))
        if request.url.host == "primary":
            return httpx.Response(429)
        return ok_reply("from fallback")

    fake_server(handler)
    assert llm.chat([{"role": "user", "content": "hi"}], model="main-model") == "from fallback"
    assert b'"model":"backup-text"' in seen[-1][1].replace(b" ", b"")  # fallback uses its own model


def test_setup_errors_do_not_fall_back(fake_server, with_fallback):
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(404)

    fake_server(handler)
    with pytest.raises(llm.LLMError, match="ollama pull"):
        llm.chat([{"role": "user", "content": "hi"}], model="missing")
    assert hosts == ["primary"]  # a missing model won't be fixed by another provider


def test_image_requests_skip_a_fallback_without_vision(fake_server, with_fallback):
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(503)

    fake_server(handler)
    image_message = [{"role": "user", "content": [{"type": "text", "text": "describe"}]}]
    with pytest.raises(llm.ProviderUnavailable):
        llm.chat(image_message)
    assert set(hosts) == {"primary"}
