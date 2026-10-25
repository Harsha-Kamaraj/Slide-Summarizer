"""Optional extras added to a summary: likely exam questions and flashcards.

Each extra is generated from the finished summary rather than from the raw
slides: the summary is already checked to follow the slides and is far
shorter, so this costs one quick call per extra instead of another pass over
the whole deck.
"""

import logging
from pathlib import Path
from string import Template

from backend import llm
from backend.pdf_builder import parse_summary

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# name -> (prompt file, how many items to ask for in each mode)
EXTRAS = {
    "exam_questions": ("extras_exam_questions", {"short": 5, "detailed": 8}),
    "flashcards": ("extras_flashcards", {"short": 10, "detailed": 15}),
}


def prompt_name(extra: str) -> str:
    return EXTRAS[extra][0]


def generate_extra(extra: str, summary: str, mode: str) -> str:
    """Return the extra as a Markdown section ready to append to the summary,
    or "" if the model's answer couldn't be used."""
    prompt_file, counts = EXTRAS[extra]
    system = (PROMPTS_DIR / "system.txt").read_text()
    prompt = Template((PROMPTS_DIR / f"{prompt_file}.txt").read_text()).substitute(
        title=parse_summary(summary).title or "this lecture",
        count=counts[mode],
        summary=summary,
    )
    section = llm.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    ).strip()

    # A card or question whose answer is "not covered in slides" teaches
    # nothing; drop those lines.
    section = "\n".join(
        line for line in section.splitlines() if "not covered in slides" not in line.lower()
    )

    # Check the answer actually parses into items before using it. A missing
    # extra is better than a broken section in the student's PDF.
    parsed = parse_summary(f"# x\n{section}")
    items = parsed.exam_questions if extra == "exam_questions" else parsed.flashcards
    if not items:
        logger.warning("Model output for %s had no usable items; skipping it", extra)
        return ""
    logger.info("Generated %d %s", len(items), extra.replace("_", " "))
    return section
