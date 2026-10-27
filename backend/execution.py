"""Job-scoped progress and a cooperative time budget, inherited by workers."""

import time
from contextlib import contextmanager
from contextvars import ContextVar, copy_context

_progress = ContextVar("progress", default=lambda message: None)
_deadline = ContextVar("deadline", default=None)
_phase = ContextVar("phase", default="")
_cancel = ContextVar("cancel", default=None)


class Cancelled(Exception):
    pass


@contextmanager
def cancellable(event):
    token = _cancel.set(event)
    try:
        checkpoint()
        yield
    finally:
        _cancel.reset(token)


def checkpoint():
    event = _cancel.get()
    if event is not None and event.is_set():
        raise Cancelled("Cancelled. Completed cached sections are kept.")


def sleep(seconds, sleeper=time.sleep):
    event = _cancel.get()
    if event is None:
        sleeper(seconds)
    else:
        event.wait(seconds)
        checkpoint()


class DeadlineExceeded(Exception):
    pass


@contextmanager
def scope(progress, seconds):
    p = _progress.set(progress)
    d = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _progress.reset(p)
        _deadline.reset(d)


def report(message):
    checkpoint()
    label = _phase.get()
    _progress.get()(f"{label}: {message}" if label else message)


@contextmanager
def phase(label):
    token = _phase.set(label)
    try:
        yield
    finally:
        _phase.reset(token)


def remaining():
    checkpoint()
    deadline = _deadline.get()
    if deadline is None:
        return float("inf")
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise DeadlineExceeded(
            "This job reached its processing time limit. Retry to reuse saved sections."
        )
    return seconds


def parallel_map(pool, function, items):
    # ContextVars do not automatically cross ThreadPoolExecutor boundaries.
    futures = [pool.submit(copy_context().run, function, item) for item in items]
    return [future.result() for future in futures]
