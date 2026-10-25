"""Turn image-heavy slides into text with a vision model.

Slides that are mostly a diagram, screenshot or picture have almost no
extractable text, so the text-only summarizer would skip what they teach.
This step renders just those slides, asks the vision model to describe their
teaching content, and adds the description to the slide's text. Everything
after this step (map-reduce, PDF) is unchanged: it just sees more text.
"""

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Template

from backend import config, llm
from backend.extract import Deck, Slide
from backend.render import render_slides

logger = logging.getLogger(__name__)

PROMPT = Template((Path(__file__).parent / "prompts" / "vision_slide.txt").read_text())
# The model's answer when a slide is only a logo, photo, credits, etc.
NO_CONTENT = "NO_CONTENT"


def describe_image_slides(deck: Deck, path: str | Path) -> int:
    """Add vision-model descriptions to the deck's image-heavy slides.

    Returns how many slides got a description.
    """
    targets = [s for s in deck.slides if s.is_image_heavy][: config.MAX_VISION_SLIDES]
    if not targets:
        return 0
    logger.info("Describing %d image-heavy slides with %s", len(targets), config.VISION_MODEL)
    images = render_slides(path, [s.number for s in targets])

    def describe(slide: Slide) -> str | None:
        if slide.number not in images:
            return None
        try:
            return _ask_vision(deck.title, slide, images[slide.number])
        except llm.LLMError as exc:
            # One unreadable slide shouldn't sink the whole summary; the text
            # step will still run (and report if the model is down entirely).
            logger.warning("Vision failed for slide %d: %s", slide.number, exc)
            return None

    with ThreadPoolExecutor(max_workers=max(1, config.LLM_PARALLEL_CALLS)) as pool:
        descriptions = list(pool.map(describe, targets))

    described = 0
    for slide, description in zip(targets, descriptions):
        if not description or description.strip().strip(".").upper() == NO_CONTENT:
            continue
        # Label it so the summarizer knows this came from the picture.
        addition = f"[From the slide image] {description.strip()}"
        slide.text = f"{slide.text}\n{addition}" if slide.text else addition
        described += 1
    logger.info("Added descriptions for %d of %d image-heavy slides", described, len(targets))
    return described


def _ask_vision(deck_title: str, slide: Slide, png: bytes) -> str:
    prompt = PROMPT.substitute(
        number=slide.number,
        deck_title=deck_title,
        slide_text=" ".join(f"{slide.title} {slide.text}".split()) or "(none)",
    )
    image_url = "data:image/png;base64," + base64.b64encode(png).decode()
    # OpenAI-compatible multimodal message: text and image parts together.
    # Ollama and hosted vision models accept the same shape.
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    return llm.chat(messages, model=config.VISION_MODEL)
