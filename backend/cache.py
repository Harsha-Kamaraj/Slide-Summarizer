"""Cache finished summaries so the same deck is only summarized once.

Many students in a class summarize the same lecture. The first one waits for
the model; everyone after gets the saved summary instantly. This is also what
keeps a hosted free-tier LLM within its daily limits.

What's stored is the Markdown summary, never the uploaded file (privacy), and
not the PDF either: rebuilding the PDF takes milliseconds, so layout changes
apply to cached summaries without re-running the model.
"""

import hashlib
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend import config

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Bump when extraction or summarization *code* changes in a way that should
# invalidate old summaries. Prompt and model changes are detected automatically.
PIPELINE_VERSION = "1"


def cache_key(content_hash: str, mode: str) -> str:
    """Key on the file's bytes, not its URL or name: PESU gives a different
    signed URL on every click, but the same lecture has the same bytes.

    The key also covers everything that changes the output (mode, prompts,
    models), so editing a prompt or switching models never serves a stale
    summary written under the old setup.
    """
    parts = [
        content_hash,
        mode,
        PIPELINE_VERSION,
        config.NOTES_MODEL,
        config.TEXT_MODEL,
        f"vision={config.VISION_ENABLED}:{config.VISION_MODEL}",
    ]
    for prompt in sorted(PROMPTS_DIR.glob("*.txt")):
        parts.append(hashlib.sha256(prompt.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def get(key: str) -> str | None:
    path = _path(key)
    return path.read_text() if path.exists() else None


def put(key: str, summary: str) -> None:
    path = _path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file and rename: a crash mid-write can't leave a
    # half-written summary that later requests would serve.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(summary)
    tmp.replace(path)


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
