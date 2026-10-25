"""The only module that talks to a language model.

Everything goes through the OpenAI-compatible Chat Completions API. Ollama
serves it at /v1 and so do most hosted providers, so switching providers is a
config change (LLM_BASE_URL, LLM_API_KEY, model names), not a code change.
"""

import logging

import httpx

from backend import config

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """The model call failed. The message says what the user can do about it."""


def chat(messages: list[dict], model: str | None = None, temperature: float = 0.2) -> str:
    """Send a chat request and return the assistant's reply text.

    Low temperature by default: for summaries we want faithful, repeatable
    output, not creative writing.
    """
    model = model or config.TEXT_MODEL
    payload: dict = {"model": model, "messages": messages, "temperature": temperature}
    if config.LLM_REASONING_EFFORT:
        payload["reasoning_effort"] = config.LLM_REASONING_EFFORT

    data = _post_with_retries(payload, model)
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response from the model: {data!r:.200}") from exc


def _post_with_retries(payload: dict, model: str) -> dict:
    url = f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
    attempts = config.LLM_MAX_RETRIES + 1

    for attempt in range(1, attempts + 1):
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT_SECONDS
            )
        except httpx.ConnectError as exc:
            # Retrying won't help if nothing is listening.
            raise LLMError(
                f"Can't reach the LLM server at {config.LLM_BASE_URL}. "
                "If you're using Ollama locally, start it with 'ollama serve'."
            ) from exc
        except httpx.TimeoutException as exc:
            # Big decks on a laptop can be slow; a retry often succeeds once
            # the model is loaded into memory.
            if attempt == attempts:
                raise LLMError(
                    f"The model timed out after {attempts} attempts. "
                    "Try a smaller model or raise LLM_TIMEOUT_SECONDS."
                ) from exc
            logger.warning("LLM timeout (attempt %d/%d), retrying", attempt, attempts)
            continue

        if response.status_code == 404:
            raise LLMError(
                f"Model '{model}' was not found. If you're using Ollama, run 'ollama pull {model}'."
            )
        if response.status_code >= 500 and attempt < attempts:
            logger.warning(
                "LLM server error %d (attempt %d/%d), retrying",
                response.status_code,
                attempt,
                attempts,
            )
            continue
        if response.status_code >= 400:
            raise LLMError(f"LLM request failed ({response.status_code}): {response.text[:200]}")
        return response.json()

    raise AssertionError("unreachable")  # the loop always returns or raises
