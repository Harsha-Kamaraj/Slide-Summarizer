"""Per-student daily limit on *new* summaries.

A hosted free-tier LLM has a daily token budget shared by everyone, so one
student summarizing 50 decks shouldn't use up the class's quota. Cached
summaries cost nothing and are never limited.

Counts live in memory and reset each day (and on restart). That's enough for
a single server; several servers would need a shared store such as Redis.
"""

import threading
from datetime import date

from backend import config

_counts: dict[tuple[str, date], int] = {}
_lock = threading.Lock()


def allow(client: str) -> bool:
    """Count one new summary for this client; False if over today's limit."""
    if config.DAILY_LIMIT <= 0:
        return True
    today = date.today()
    with _lock:
        # Drop earlier days' counts so the dict doesn't grow forever.
        for key in [k for k in _counts if k[1] != today]:
            del _counts[key]
        used = _counts.get((client, today), 0)
        if used >= config.DAILY_LIMIT:
            return False
        _counts[(client, today)] = used + 1
        return True


def reset() -> None:
    with _lock:
        _counts.clear()
