"""Conservative, process-wide request reservations per provider/model.

Tokens are estimates, not a tokenizer guarantee. Provider 429 responses remain
authoritative. API keys are deliberately absent from keys and log messages.
"""

import math
import threading
import time
from collections import defaultdict, deque

from backend import config, execution


def estimate_tokens(messages):
    count = 32
    for message in messages:
        content = message.get("content", "")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for part in parts:
            if part.get("type") == "image_url":
                count += config.LLM_IMAGE_TOKEN_ESTIMATE
            else:
                # Conservative for English/code; UTF-8 bytes also cover Unicode.
                count += math.ceil(len(str(part.get("text", "")).encode()) / 3)
        count += 8
    return count


class Limiter:
    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.events = defaultdict(deque)
        self.cooldowns = {}

    def defer(self, key, seconds):
        with self.lock:
            self.cooldowns[key] = max(self.cooldowns.get(key, 0), self.clock() + seconds)

    def acquire(self, key, tokens):
        tpm, rpm = config.LLM_TOKENS_PER_MINUTE, config.LLM_REQUESTS_PER_MINUTE
        if not tpm and not rpm:
            return
        while True:
            execution.remaining()
            with self.lock:
                now = self.clock()
                events = self.events[key]
                while events and events[0][0] <= now - 60:
                    events.popleft()
                wait = max(0, self.cooldowns.get(key, 0) - now)
                over = (rpm and len(events) >= rpm) or (
                    tpm and sum(n for _, n in events) + tokens > tpm
                )
                if over and events:
                    wait = max(wait, events[0][0] + 60 - now)
                if wait <= 0:
                    events.append((now, tokens))
                    return
            execution.report(f"Waiting for AI allowance ({math.ceil(wait)}s); saved work is kept…")
            self.sleep(min(wait, 5, execution.remaining()))


limiter = Limiter()
