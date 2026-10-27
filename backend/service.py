"""The request pipeline shared by the API endpoints: cached summary, then
cached extras, combined into one Markdown document."""

import logging
from collections.abc import Callable
from pathlib import Path

from backend import cache, config, execution, figures
from backend.extras import generate_extra
from backend.summarize import summarize_file

logger = logging.getLogger(__name__)

Progress = Callable[[str], None]


def no_progress(_: str) -> None:
    pass


def run(
    path: Path,
    content_hash: str,
    mode: str,
    extras: list[str],
    progress: Progress = no_progress,
) -> tuple[str, bool]:
    with execution.scope(progress, config.JOB_MAX_SECONDS):
        return _run(path, content_hash, mode, extras, progress)


def _run(path, content_hash, mode, extras, progress):
    """Return (markdown, from_cache). from_cache is True only if nothing had
    to be generated."""
    generated = False
    execution.checkpoint()

    key = cache.cache_key(content_hash, mode)
    with cache.lock(key):
        execution.checkpoint()
        summary = cache.get(key)
        if summary is None:
            summary = summarize_file(path, mode=mode, progress=progress)
            cache.put(key, summary)
            generated = True
        else:
            progress("Reusing saved summary…")
            if config.INCLUDE_DIAGRAMS and figures.needs_upgrade(summary):
                from backend.extract import extract_deck
                from backend.pdf_builder import parse_summary

                # Keep good summary/extras text. Upgrade only the old appendix.
                clean = figures.MARKER.sub("", summary).rstrip()
                marker = figures.prepare(
                    extract_deck(path), path, progress, parse_summary(clean).topics
                )
                summary = clean + ("\n\n" + marker if marker else "")
                cache.put(key, summary)
                generated = True

    sections = []
    for extra in extras:
        execution.remaining()
        key = cache.cache_key(content_hash, mode, extra)
        with cache.lock(key):
            execution.checkpoint()
            section = cache.get(key)
            if section is None:
                progress(f"Writing {extra.replace('_', ' ')}…")
                with execution.phase(f"Writing {extra.replace('_', ' ')}"):
                    section = generate_extra(extra, summary, mode)
                generated = True
                if section:  # don't cache a failed extra; retry it next time
                    cache.put(key, section)
            else:
                progress(f"Reusing saved {extra.replace('_', ' ')}…")
        if section:
            sections.append(section)

    return "\n\n".join([summary, *sections]), not generated


def summary_id(content_hash: str, mode: str) -> str:
    """A stable id for a deck's summary (its cache key), used to attach
    feedback. It's a hash, so it reveals nothing about the file."""
    return cache.cache_key(content_hash, mode)


def is_cached(content_hash: str, mode: str, extras: list[str]) -> bool:
    """Would this request be answered entirely from the cache?"""
    parts = ["summary", *extras]
    return all(cache.get(cache.cache_key(content_hash, mode, part)) for part in parts)
