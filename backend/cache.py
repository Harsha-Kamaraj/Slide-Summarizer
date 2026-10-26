"""Cache finished summaries so the same deck is only summarized once.

Many students in a class summarize the same lecture. The first one waits for
the model; everyone after gets the saved summary instantly. This is also what
keeps a hosted free-tier LLM within its daily limits.

Stored entries include generated text, successful AI sections, job records and
selected source images. The full uploaded file and generated PDF aren't kept;
PDFs are rebuilt so layout fixes apply without repeating AI work.
"""

import hashlib
import json
import logging
import threading
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import EntryNotFoundError

from backend import config, extras

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Bump when extraction or summarization *code* changes in a way that should
# invalidate old summaries. Prompt and model changes are detected automatically.
PIPELINE_VERSION = "3"  # source figures, bounded requests and resumable sections


# Prompt files that shape the summary itself. Extras have their own prompt, so
# tweaking the flashcard prompt doesn't throw away every cached summary.
SUMMARY_PROMPTS = ("system", "chunk_notes", "final_summary", "vision_slide")


def cache_key(content_hash: str, mode: str, part: str = "summary") -> str:
    """Key on the file's bytes, not its URL or name: PESU gives a different
    signed URL on every click, but the same lecture has the same bytes.

    `part` is "summary" or an extra ("flashcards", ...); each is cached on its
    own, so asking for flashcards later reuses the cached summary. The key also
    covers everything that changes that part's output (mode, prompts, models),
    so editing a prompt or switching models never serves a stale result.
    """
    parts = [content_hash, mode, part, PIPELINE_VERSION, config.TEXT_MODEL]
    if part == "summary":
        parts += [config.NOTES_MODEL, f"vision={config.VISION_ENABLED}:{config.VISION_MODEL}"]
        parts += [
            str(x)
            for x in (
                config.MAX_VISION_SLIDES,
                config.VISION_MAX_TOKENS,
                config.CHUNK_SIZE,
                config.NOTES_MAX_TOKENS,
                config.LLM_INPUT_TOKEN_BUDGET,
                config.LLM_OUTPUT_TOKEN_BUDGET,
                config.INCLUDE_DIAGRAMS,
                config.MAX_DIAGRAM_SLIDES,
            )
        ]
        prompts = SUMMARY_PROMPTS
    else:
        # Extras derive from the summary: a summary change invalidates them too.
        parts.append(cache_key(content_hash, mode))
        prompts = ("system", extras.prompt_name(part))
    for name in prompts:
        parts.append(hashlib.sha256((PROMPTS_DIR / f"{name}.txt").read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def get(key: str) -> str | None:
    """Local disk first; then the shared remote store, if configured (a
    remote hit is copied to disk so the next lookup is local)."""
    path = _path(key)
    if path.exists():
        return path.read_text()
    summary = _remote_get(key)
    if summary is not None:
        _write_local(path, summary)
    return summary


def put(key: str, summary: str) -> None:
    _write_local(_path(key), summary)
    if config.CACHE_HF_REPO:
        # Finish the remote write before reporting saved work. A daemon thread
        # could be killed during a deploy, silently losing the only durable copy.
        _remote_put(key, summary)


def object_key(kind: str, data) -> str:
    return hashlib.sha256(
        json.dumps([kind, data], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _write_local(path: Path, summary: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file and rename: a crash mid-write can't leave a
    # half-written summary that later requests would serve.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
        tmp = Path(out.name)
        out.write(summary)
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


# --- Remote store: a private Hugging Face dataset ------------------------
# Free hosts like Render wipe the disk whenever the service sleeps or
# restarts, which would throw the cache away several times a day. A private
# dataset repo is free, persistent storage. Remote failures are logged and
# treated as a miss: the cache must never break summarizing.


def _remote_get(key: str) -> str | None:
    if not config.CACHE_HF_REPO:
        return None
    try:
        file = hf_hub_download(
            config.CACHE_HF_REPO, f"{key}.md", repo_type="dataset", token=config.HF_TOKEN or None
        )
        return Path(file).read_text()
    except EntryNotFoundError:
        return None
    except Exception as exc:  # network trouble, auth, outage: just a miss
        logger.warning("Remote cache read failed: %s", exc)
        return None


_remote_write_lock = threading.Lock()


def _remote_put(key: str, summary: str) -> None:
    try:
        # Parallel completions must not race commits to the same dataset.
        with _remote_write_lock:
            HfApi(token=config.HF_TOKEN or None).upload_file(
                path_or_fileobj=summary.encode(),
                path_in_repo=f"{key}.md",
                repo_id=config.CACHE_HF_REPO,
                repo_type="dataset",
                commit_message="Cache summary checkpoint",
            )
    except Exception as exc:
        logger.warning("Remote cache write failed: %s", exc)


# One lock per key. If two students click the same deck at once, the second
# waits for the first to finish and then gets a cache hit, instead of both
# running the model for minutes.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@contextmanager
def lock(key: str) -> Iterator[None]:
    with _locks_guard:
        key_lock = _locks.setdefault(key, threading.Lock())
    with key_lock:
        yield


def _path(key: str) -> Path:
    return Path(config.CACHE_DIR) / f"{key}.md"
