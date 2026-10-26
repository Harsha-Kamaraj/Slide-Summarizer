"""Summarize a lecture deck with map-reduce over chunks of slides.

CLI:  python -m backend.summarize path/to/deck.pptx [--mode short|detailed] [-o summary.pdf]
"""

import argparse
import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Template

from backend import config, llm
from backend.pdf_builder import build_pdf, parse_summary
from backend.extract import Deck, ExtractionError, Slide, extract_deck
from backend.extras import generate_extra
from backend.vision import describe_image_slides

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# How much the final merge should keep, per mode.
LENGTH_INSTRUCTIONS = {
    "short": (
        "Keep it short for quick revision: at most 4 topics with 3-4 one-sentence bullets each."
    ),
    "detailed": (
        "Be thorough: this replaces reading the slides. Cover every distinct idea, definition, "
        "formula and algorithm step in the notes, but explain rather than transcribe. Each "
        "bullet is 1-3 full sentences that explain the point: what it means, why it matters, "
        "and the lecture's example if there is one. Never write a bullet that is only a label "
        "(like 'Example:' or 'Data:'); fold short lists into the sentence that introduces them "
        "(e.g. 'The four combinations are RR, RL, GR and GL.'). Aim for about 8-12 topics: merge "
        "sections about the same idea into one topic (e.g. a concept and its key elements or "
        "applications belong together). For algorithms, put nested sub-steps "
        "inside their numbered step instead of separate bullets. Write each worked example or "
        "practice problem as its own topic (e.g. '### Worked Example: ...') with the problem, "
        "the steps and the final answer. Include limitations and comparisons the notes "
        "mention. Keep every topic from the notes: never drop one to save space."
    ),
}

# How much detail the map step should keep. The merge can't add detail the
# notes dropped (without inventing it), so detailed mode needs richer notes.
NOTES_INSTRUCTIONS = {
    "short": "Keep the notes concise: main ideas and definitions only.",
    "detailed": (
        "Be complete but explain, don't transcribe: keep every explanation, definition, "
        "formula, algorithm step and worked example (with its steps and final answer), written "
        "as full sentences in your own words. Combine short slide fragments into sentences "
        "instead of copying them line by line. Every slide with teaching content must appear "
        "in your notes, including quotes and definitions, practice problems with any solution "
        "the slides give, and open questions the slides raise ('points to ponder'). Copy table "
        "headers, attribute names and values exactly as the slides write them."
    ),
}


def load_prompt(name: str) -> Template:
    return Template((PROMPTS_DIR / f"{name}.txt").read_text())


def summarize_file(
    path: str | Path, mode: str = "detailed", progress: Callable[[str], None] | None = None
) -> str:
    """The whole pipeline for one file: extract text, describe image-heavy
    slides with the vision model, then summarize. Used by the CLI and the API.

    `progress` receives short status messages ("Summarizing part 3/8") that
    the extension shows to students while they wait.
    """
    report = progress or (lambda _: None)
    report("Reading slides…")
    deck = extract_deck(path)
    if config.VISION_ENABLED:
        heavy = sum(s.is_image_heavy for s in deck.slides)
        if heavy:
            report(f"Reading {min(heavy, config.MAX_VISION_SLIDES)} diagram slides…")
            describe_image_slides(deck, path)
    return summarize_deck(deck, mode=mode, progress=report)


def summarize_deck(
    deck: Deck, mode: str = "detailed", progress: Callable[[str], None] | None = None
) -> str:
    """Return a Markdown summary of the deck."""
    if mode not in LENGTH_INSTRUCTIONS:
        raise ValueError(f"mode must be one of {list(LENGTH_INSTRUCTIONS)}")

    # Slides with no text at all (pure images) give the text model nothing to
    # work with. Stage 5 sends them to the vision model instead.
    slides = [s for s in deck.slides if s.title or s.text or s.notes]
    skipped = len(deck.slides) - len(slides)
    if skipped:
        logger.info("Skipping %d slides with no text (images only)", skipped)
    if not slides:
        raise ExtractionError(
            "No text found in this file. It may be scanned images, which isn't supported yet."
        )

    system = load_prompt("system").template
    chunks = [slides[i : i + config.CHUNK_SIZE] for i in range(0, len(slides), config.CHUNK_SIZE)]

    # Map: each chunk of slides becomes revision notes. Chunks don't depend on
    # each other, so they can run in parallel; map() keeps the results in order.
    chunk_prompt = load_prompt("chunk_notes")

    report = progress or (lambda _: None)
    done = 0
    done_lock = threading.Lock()  # chunks may finish on parallel threads

    def chunk_notes(numbered_chunk: tuple[int, list[Slide]]) -> str:
        nonlocal done
        i, chunk = numbered_chunk
        logger.info(
            "Summarizing chunk %d/%d (slides %d-%d)",
            i,
            len(chunks),
            chunk[0].number,
            chunk[-1].number,
        )

        def prompt_for(text):
            return chunk_prompt.substitute(
                first=chunk[0].number,
                last=chunk[-1].number,
                deck_title=deck.title,
                detail_instructions=NOTES_INSTRUCTIONS[mode],
                slides=text,
            )

        notes = _notes_with_splitting(system, prompt_for, format_slides(chunk), report)
        with done_lock:
            done += 1
            if done < len(chunks):
                report(f"Summarizing part {done + 1}/{len(chunks)}…")
        return notes

    report(f"Summarizing part 1/{len(chunks)}…")
    with ThreadPoolExecutor(max_workers=max(1, config.LLM_PARALLEL_CALLS)) as pool:
        notes = list(pool.map(chunk_notes, enumerate(chunks, start=1)))
    terms = collect_terms(chunks, notes, limit=TERMS_LIMIT[mode])

    # Reduce: merge all chunk notes into one structured document.
    logger.info("Merging %d sections into the final summary", len(notes))
    report("Putting the summary together…")
    summary = _summary_with_splitting(system, deck.title, mode, "\n\n".join(notes), report)
    if terms:
        lines = [f"- **{term}**: {definition}" for term, definition in terms]
        summary = summary.rstrip() + "\n\n## Important Terms\n" + "\n".join(lines)
    return summary


def _split_oversized_text(text: str, depth: int) -> tuple[str, str]:
    """Split without truncation, preferring paragraph/word boundaries.

    Provider token counts vary by model. React to a size rejection rather
    than assuming that a fixed slide count or character estimate will fit.
    Bound recursion in case even the fixed prompt exceeds a provider limit.
    """
    if depth >= 6 or len(text) < 200:
        raise llm.RequestTooLarge(
            "The AI service still rejects this text after splitting it into smaller sections. "
            "Try a smaller deck or ask the administrator to check the model limits."
        )
    middle = len(text) // 2
    cut = text.rfind("\n\n", middle // 2, middle)
    if cut < 0:
        cut = text.rfind(" ", middle // 2, middle)
    if cut < 0:
        cut = middle
    return text[:cut], text[cut:]


def _notes_with_splitting(system, prompt_for, text, report, depth=0):
    try:
        return _ask(system, prompt_for(text), model=config.NOTES_MODEL)
    except llm.RequestTooLarge:
        left, right = _split_oversized_text(text, depth)
        report("Processing smaller sections to fit the AI limit…")
        return "\n\n".join(
            _notes_with_splitting(system, prompt_for, part, report, depth + 1)
            for part in (left, right)
        )


def _summary_with_splitting(system, title, mode, notes, report, depth=0):
    try:
        return _final_summary(system, title, mode, notes)
    except llm.RequestTooLarge:
        left, right = _split_oversized_text(notes, depth)
        report("Building the summary in smaller sections to fit the AI limit…")
        parts = [
            _summary_with_splitting(system, title, mode, part, report, depth + 1)
            for part in (left, right)
        ]
        # Don't feed all the results back into another oversized model call.
        # Assemble their overviews and topics locally, retaining every part.
        overviews, topics = [], []
        for part in parts:
            if structure_problem(part):
                raise llm.LLMError("The AI returned an invalid summary section. Please try again.")
            overview, points = re.split(r"(?m)^## Key Points\s*$", part, maxsplit=1)
            overviews.append(re.split(r"(?m)^## Overview\s*$", overview, maxsplit=1)[1].strip())
            topics.append(points.strip())
        heading = next(line for line in parts[0].splitlines() if line.startswith("# "))
        return (
            heading
            + "\n\n## Overview\n"
            + "\n\n".join(overviews)
            + "\n\n## Key Points\n"
            + "\n\n".join(topics)
        )


def _final_summary(system, title, mode, notes):
    final_prompt = load_prompt("final_summary").substitute(
        deck_title=title,
        length_instructions=LENGTH_INSTRUCTIONS[mode],
        notes=notes,
    )
    summary = _ask(system, final_prompt)

    # The PDF builder relies on each section heading appearing exactly once.
    # Small models occasionally repeat the template mid-document, so give the
    # model one chance to fix it rather than shipping a malformed summary.
    problem = structure_problem(summary)
    if problem:
        logger.warning("Summary structure invalid (%s); asking the model to fix it", problem)
        summary = llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": final_prompt},
                {"role": "assistant", "content": summary},
                {"role": "user", "content": FIX_STRUCTURE_PROMPT.format(problem=problem)},
            ]
        )
        problem = structure_problem(summary)
        if problem:
            logger.warning("Summary structure still invalid (%s); using it anyway", problem)
    return summary


TERMS_LIMIT = {"short": 8, "detailed": 15}


def collect_terms(chunks: list[list[Slide]], notes: list[str], limit: int) -> list[tuple[str, str]]:
    """The Important Terms list, built in code rather than by the merge model.

    Each chunk's notes list the terms its slides define. A term is kept only
    if its definition is grounded in that chunk's slides (most of its key
    words appear there): when asked for a terms list, models write textbook
    definitions for words the slides merely mention. Duplicates across
    chunks keep their first definition; order follows the lecture.
    """
    kept: dict[str, tuple[str, str]] = {}
    for chunk, chunk_notes in zip(chunks, notes):
        slide_words = _content_words(" ".join(f"{s.title} {s.text} {s.notes}" for s in chunk))
        for term, definition in _parse_terms(chunk_notes):
            key = " ".join(sorted(_content_words(term))) or term.lower()
            if key in kept or not _grounded(definition, slide_words):
                continue
            kept[key] = (term, definition)
    return list(kept.values())[:limit]


def _parse_terms(notes: str) -> list[tuple[str, str]]:
    """The "- Term: definition" bullets after a chunk's "Terms:" line."""
    terms = []
    in_terms = False
    for line in notes.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("terms:"):
            in_terms = True
            continue
        if in_terms and stripped.startswith(("- ", "* ")):
            term, sep, definition = stripped[2:].replace("**", "").partition(":")
            if sep and term.strip() and definition.strip():
                terms.append((term.strip(), definition.strip()))
        elif in_terms and stripped.startswith("#"):
            in_terms = False  # a new topic heading ends the terms list
    return terms


_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = set(
    "the a an and or of to in on for is are be by with as that this it its from at which "
    "can each into used use their they them than then these those has have not all any "
    "set such when where what how".split()
)


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _grounded(definition: str, slide_words: set[str]) -> bool:
    """Most of the definition's key words appear on the slides."""
    words = _content_words(definition)
    return bool(words) and len(words & slide_words) / len(words) >= 0.5


# The model writes these; "## Important Terms" is appended in code afterwards.
REQUIRED_SECTIONS = ("## Overview", "## Key Points")

FIX_STRUCTURE_PROMPT = (
    "Your answer did not follow the required structure: {problem}. "
    "Rewrite it as ONE document: a single '# ' title line, then each of "
    "'## Overview' and '## Key Points' exactly once, in that order, and no other '## ' sections. "
    "Put every topic under '## Key Points' as a '### ' heading. Keep all the content."
)


def structure_problem(summary: str) -> str | None:
    """Return what's wrong with the summary's heading structure, or None if it's fine."""
    lines = [line.strip() for line in summary.splitlines()]
    titles = [line for line in lines if line.startswith("# ")]
    if len(titles) != 1:
        return f"expected 1 title line starting with '# ', found {len(titles)}"
    level2 = [line for line in lines if line.startswith("## ")]
    if tuple(level2) != REQUIRED_SECTIONS:
        return f"expected sections {list(REQUIRED_SECTIONS)}, found {level2}"
    return None


def format_slides(slides: list[Slide]) -> str:
    """Render slides as plain text with clear boundaries the model can follow."""
    blocks = []
    for slide in slides:
        lines = [f"--- Slide {slide.number}: {slide.title or '(untitled)'} ---"]
        if slide.text:
            lines.append(slide.text)
        if slide.notes:
            lines.append(f"Speaker notes: {slide.notes}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _ask(system: str, prompt: str, model: str | None = None) -> str:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    return llm.chat(messages, model=model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize a lecture deck.")
    parser.add_argument("path", help="a .pptx, .ppt or .pdf file")
    parser.add_argument("--mode", choices=list(LENGTH_INSTRUCTIONS), default="detailed")
    parser.add_argument("-o", "--output", default="summary.pdf", help="where to write the PDF")
    parser.add_argument("--extras", nargs="*", default=[], choices=["exam_questions", "flashcards"])
    args = parser.parse_args(argv)

    # Progress goes to stderr so stdout is just the summary (pipeable to a file).
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # hide per-request lines

    start = time.perf_counter()
    try:
        summary = summarize_file(args.path, mode=args.mode)
        sections = [generate_extra(extra, summary, args.mode) for extra in args.extras]
        summary = "\n\n".join([summary, *filter(None, sections)])
    except (ExtractionError, llm.LLMError) as exc:
        logger.error("Error: %s", exc)
        return 1

    print(summary)
    Path(args.output).write_bytes(build_pdf(parse_summary(summary), Path(args.path).name))
    logger.info("Wrote %s", args.output)
    logger.info("Done in %.1fs", time.perf_counter() - start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
