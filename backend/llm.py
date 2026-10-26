"""The only module that talks to a language model.

Everything goes through the OpenAI-compatible Chat Completions API. Ollama
serves it at /v1 and so do most hosted providers, so switching providers is a
config change (LLM_BASE_URL, LLM_API_KEY, model names), not a code change.

An optional fallback provider (FALLBACK_* settings) takes over when the main
one is unavailable: rate-limited, down, or timing out. Free hosted tiers hit
their per-minute limits at busy times, so this keeps students from seeing
"try again later" during exam week.
"""

import logging
import re
import time
from dataclasses import dataclass

import httpx

from backend import config

logger = logging.getLogger(__name__)

# Upper bound on how long we honour a provider's Retry-After header.
MAX_RETRY_AFTER_SECONDS = 30
_sleep = time.sleep  # replaced in tests


class LLMError(Exception):
    """The model call failed. The message says what the user can do about it."""


class ProviderUnavailable(LLMError):
    """The provider is rate-limited, down or too slow; another one might work.
    (Unlike e.g. a missing model, which is a setup problem to report.)"""


class RequestTooLarge(ProviderUnavailable):
    """The caller must split its input; waiting won't make this request fit."""


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    reasoning_effort: str
    # The fallback runs its own models; None means "use the model asked for".
    text_model: str | None = None
    vision_model: str | None = None


def providers() -> list[Provider]:
    found = [
        Provider("primary", config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_REASONING_EFFORT)
    ]
    if config.FALLBACK_BASE_URL:
        found.append(
            Provider(
                "fallback",
                config.FALLBACK_BASE_URL,
                config.FALLBACK_API_KEY,
                config.FALLBACK_REASONING_EFFORT,
                text_model=config.FALLBACK_MODEL or None,
                vision_model=config.FALLBACK_VISION_MODEL or None,
            )
        )
    return found


def chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> str:
    """Send a chat request and return the assistant's reply text.

    Low temperature by default: for summaries we want faithful, repeatable
    output, not creative writing. `max_tokens` caps the reply length; some
    hosted free tiers reject requests whose possible output exceeds their
    per-minute output limit, so short-answer calls should set it.
    """
    requested = model or config.TEXT_MODEL
    has_images = any(isinstance(m["content"], list) for m in messages)
    error: LLMError | None = None

    for provider in providers():
        if provider.name == "primary":
            use_model = requested
        else:
            use_model = provider.vision_model if has_images else provider.text_model
            if use_model is None:
                continue  # this fallback has no model for this kind of request
        payload: dict = {"model": use_model, "messages": messages, "temperature": temperature}
        if provider.reasoning_effort:
            payload["reasoning_effort"] = provider.reasoning_effort
        if max_tokens:
            payload["max_tokens"] = max_tokens
        try:
            data = _post_with_retries(provider, payload)
        except RequestTooLarge:
            # Let the summarization pipeline split the source without
            # discarding content or switching model quality unnecessarily.
            raise
        except ProviderUnavailable as exc:
            logger.warning("%s LLM provider unavailable: %s", provider.name, exc)
            error = exc
            continue
        try:
            return strip_thinking(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response from the model: {data!r:.200}") from exc

    raise error or LLMError("No LLM provider is configured.")


# Some open models put their private reasoning in the reply itself, e.g.
# Gemma via Google's API ("<thought>…</thought>") or Qwen/DeepSeek
# ("<think>…</think>"), and not every provider lets us switch it off. It must
# never reach a student's PDF.
_THINKING = re.compile(r"<(thought|think)>.*?(</\1>|\Z)", re.IGNORECASE | re.DOTALL)


def strip_thinking(text: str) -> str:
    """Remove reasoning blocks; an unclosed one (reply cut off mid-thought)
    is removed to the end."""
    return _THINKING.sub("", text).strip()


def _post_with_retries(provider: Provider, payload: dict) -> dict:
    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {provider.api_key}"}
    model = payload["model"]
    attempts = config.LLM_MAX_RETRIES + 1

    for attempt in range(1, attempts + 1):
        last_attempt = attempt == attempts
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT_SECONDS
            )
        except httpx.ConnectError as exc:
            # Retrying won't help if nothing is listening.
            raise ProviderUnavailable(
                f"Can't reach the LLM server at {provider.base_url}. "
                "If you're using Ollama locally, start it with 'ollama serve'."
            ) from exc
        except httpx.TimeoutException as exc:
            # Big decks on a laptop can be slow; a retry often succeeds once
            # the model is loaded into memory.
            if last_attempt:
                raise ProviderUnavailable(
                    f"The model timed out after {attempts} attempts. "
                    "Try a smaller model or raise LLM_TIMEOUT_SECONDS."
                ) from exc
            logger.warning("LLM timeout (attempt %d/%d), retrying", attempt, attempts)
            continue

        status = response.status_code
        if status == 413 or (status == 429 and "request too large" in response.text.lower()):
            # Over a per-minute limit in a *single* request: waiting can't
            # help, so don't spend retries on it. Avoid exposing account IDs
            # from the provider's response in a student's error message.
            logger.warning(
                "LLM request too large (provider=%s, model=%s, status=%d)",
                provider.name,
                model,
                status,
            )
            raise RequestTooLarge(
                "This request is too large for the AI service's limits. "
                "The text needs to be processed in smaller sections."
            )
        if status == 429:
            # Rate limited: wait as long as the provider asks (within reason).
            if last_attempt:
                raise ProviderUnavailable("The AI service is busy (rate limited).")
            wait = _retry_after(response, default=2**attempt)
            logger.warning("Rate limited; waiting %.0fs (attempt %d/%d)", wait, attempt, attempts)
            _sleep(wait)
            continue
        if status >= 500:
            if last_attempt:
                raise ProviderUnavailable(f"The AI service had an error ({status}).")
            logger.warning(
                "LLM server error %d (attempt %d/%d), retrying", status, attempt, attempts
            )
            continue
        if status == 404:
            raise LLMError(
                f"Model '{model}' was not found. If you're using Ollama, run 'ollama pull {model}'."
            )
        if status >= 400:
            raise LLMError(f"LLM request failed ({status}): {response.text[:200]}")
        return response.json()

    raise AssertionError("unreachable")  # the loop always returns or raises


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        seconds = float(response.headers.get("retry-after", default))
    except ValueError:  # an HTTP date instead of seconds; just use the default
        seconds = default
    return min(max(seconds, 0), MAX_RETRY_AFTER_SECONDS)
