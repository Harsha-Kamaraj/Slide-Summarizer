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
