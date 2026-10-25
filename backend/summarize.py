"""Summarize a lecture deck with map-reduce over chunks of slides.

CLI:  python -m backend.summarize path/to/deck.pptx [--mode short|detailed] [-o summary.pdf]
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Template

from backend import config, llm
from backend.pdf_builder import build_pdf, parse_summary
from backend.extract import Deck, ExtractionError, Slide, extract_deck
from backend.vision import describe_image_slides

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# How much the final merge should keep, per mode.
LENGTH_INSTRUCTIONS = {
    "short": (
        "Keep it short for quick revision: at most 4 topics with 3-4 one-sentence bullets "
        "each, and at most 8 terms."
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
        "mention. Up to 15 terms."
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
        "instead of copying them line by line."
    ),
}


def load_prompt(name: str) -> Template:
    return Template((PROMPTS_DIR / f"{name}.txt").read_text())


def summarize_file(path: str | Path, mode: str = "detailed") -> str:
    """The whole pipeline for one file: extract text, describe image-heavy
    slides with the vision model, then summarize. Used by the CLI and the API."""
    deck = extract_deck(path)
    if config.VISION_ENABLED:
        describe_image_slides(deck, path)
    return summarize_deck(deck, mode=mode)


def summarize_deck(deck: Deck, mode: str = "detailed") -> str:
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

    def chunk_notes(numbered_chunk: tuple[int, list[Slide]]) -> str:
        i, chunk = numbered_chunk
        logger.info(
            "Summarizing chunk %d/%d (slides %d-%d)",
            i,
            len(chunks),
            chunk[0].number,
            chunk[-1].number,
        )
        prompt = chunk_prompt.substitute(
            first=chunk[0].number,
            last=chunk[-1].number,
            deck_title=deck.title,
            detail_instructions=NOTES_INSTRUCTIONS[mode],
            slides=format_slides(chunk),
        )
        return _ask(system, prompt, model=config.NOTES_MODEL)

    with ThreadPoolExecutor(max_workers=max(1, config.LLM_PARALLEL_CALLS)) as pool:
        notes = list(pool.map(chunk_notes, enumerate(chunks, start=1)))

    # Reduce: merge all chunk notes into one structured document.
    logger.info("Merging %d sections into the final summary", len(notes))
    final_prompt = load_prompt("final_summary").substitute(
        deck_title=deck.title,
        length_instructions=LENGTH_INSTRUCTIONS[mode],
        notes="\n\n".join(notes),
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


REQUIRED_SECTIONS = ("## Overview", "## Key Points", "## Important Terms")

FIX_STRUCTURE_PROMPT = (
    "Your answer did not follow the required structure: {problem}. "
    "Rewrite it as ONE document: a single '# ' title line, then each of "
    "'## Overview', '## Key Points' and '## Important Terms' exactly once, in that order. "
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
    args = parser.parse_args(argv)

    # Progress goes to stderr so stdout is just the summary (pipeable to a file).
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # hide per-request lines

    start = time.perf_counter()
    try:
        summary = summarize_file(args.path, mode=args.mode)
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
