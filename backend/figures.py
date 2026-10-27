"""Attach compact, source-grounded diagrams to the topics they explain."""

import base64
import hashlib
import json
import logging
import re
from pathlib import Path

import pymupdf

from backend import config, diagrams, execution, llm
from backend.render import render_slides
from backend.extract import ExtractionError

logger = logging.getLogger(__name__)
MARKER = re.compile(r"<!-- source-figures:([0-9a-f]{64}) -->")
VERSION = 4
PROMPT = (Path(__file__).parent / "prompts" / "diagram.txt").read_text()
_STOP = set(
    "the a an and or of to in on for is are by with from slide slides diagram architecture overview introduction apache what it how deep dive".split()
)


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def match_topic(slide, topics):
    """Prefer title matches; require multiple specific words for a body match."""
    title = _words(slide.title)
    scores = []
    for i, topic in enumerate(topics):
        heading = _words(topic.heading)
        common = title & heading
        score = len(common) / max(1, len(title)) + len(common) / max(1, len(heading))
        if common:
            score += (
                0.1
                * len(_words(slide.text) & _words(" ".join(topic.points)))
                / max(1, len(_words(slide.text)))
            )
        # Generic headings (e.g. "Architecture deep dive") need multiple
        # specific topic words in the slide body, not a shared "deep dive".
        body_common = heading & _words(slide.text)
        if len(body_common) >= 2 and len(body_common) / max(1, len(heading)) >= 0.75:
            score = max(score, 0.7)
        scores.append((score, i))
    if not scores:
        return None
    score, i = max(scores, key=lambda item: (item[0], -item[1]))
    return i if score >= 0.65 else None


def prepare(deck, path, report, topics=()):
    from backend import cache

    if not config.INCLUDE_DIAGRAMS or not topics:
        return ""
    with Path(path).open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    key = cache.object_key(
        "topic-diagrams-v4",
        [
            digest,
            [t.heading for t in topics],
            config.VISION_MODEL,
            config.MAX_INLINE_DIAGRAMS,
            config.MAX_DIAGRAM_SLIDES,
            PROMPT,
        ],
    )
    saved = cache.get(key)
    if saved:
        try:
            existing = load(key)
            if existing and existing.get("version") == VERSION and not existing.get("incomplete"):
                return f"<!-- source-figures:{key} -->"
        except (ValueError, TypeError):
            pass
    candidates = []
    used_topics = set()
    # First diagram per topic, bounded so visuals do not consume an entire quota.
    for slide in deck.slides:
        if not slide.has_visual or slide.is_administrative:
            continue
        topic = match_topic(slide, topics)
        if topic is not None and topic not in used_topics:
            candidates.append((slide, topic))
            used_topics.add(topic)
    limit = max(0, min(config.MAX_INLINE_DIAGRAMS, config.MAX_DIAGRAM_SLIDES))
    # Prefer actual relationship/process titles before decorative visual slides.
    structural = re.compile(
        r"diagram|architecture|pipeline|flow|process|relationship|connector problem|capture", re.I
    )
    candidates.sort(key=lambda pair: (not bool(structural.search(pair[0].title)), pair[0].number))
    candidates = candidates[:limit]
    try:
        images = render_slides(path, [s.number for s, _ in candidates])
    except ExtractionError as exc:
        logger.warning("Could not render optional diagrams: %s", exc)
        images = {}
    selected, failures = [], []
    for slide, topic in candidates:
        if slide.number not in images:
            failures.append(slide.number)
            continue
        try:
            if execution.remaining() < 30:
                failures.append(slide.number)
                continue
            report(f"Preparing diagram for {topics[topic].heading}…")
            with execution.phase(f"Drawing diagram from slide {slide.number}"):
                item = analyze(slide, images[slide.number], topics[topic])
            if item:
                selected.append(
                    {
                        "number": slide.number,
                        "title": slide.title,
                        "topic": topics[topic].heading,
                        **item,
                    }
                )
        except (llm.LLMError, execution.DeadlineExceeded, ValueError) as exc:
            logger.warning("Diagram for slide %s unavailable: %s", slide.number, exc)
            failures.append(slide.number)
    cache.put(
        key,
        json.dumps(
            {
                "version": VERSION,
                "slides": selected,
                "incomplete": failures,
                "total": len(candidates),
            }
        ),
    )
    return f"<!-- source-figures:{key} -->"


def analyze(slide, png, topic):
    """The image is the evidence. AI returns data, not code or drawing commands."""
    prompt = (
        PROMPT + "\nTopic: " + topic.heading[:160] + "\nSource slide title: " + slide.title[:160]
    )
    prompt += "\nSlide text (excerpt): " + slide.text[:900]
    reply = llm.chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(png).decode()
                        },
                    },
                ],
            }
        ],
        model=config.VISION_MODEL,
        max_tokens=1100,
    )
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", reply.strip())
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid diagram response")
    if data.get("kind") == "none":
        return None
    caption = data.get("caption", "")
    if not isinstance(caption, str) or len(caption) > 180:
        raise ValueError("Invalid caption")
    if data.get("kind") == "graph":
        try:
            graph = diagrams.validate(data)
            return {"kind": "graph", "graph": graph, "caption": caption}
        except ValueError:
            # A graph we cannot lay out safely must keep its original notation.
            if not data.get("bbox"):
                raise
    if data.get("kind") not in ("graph", "crop"):
        raise ValueError("Unknown diagram type")
    cropped = crop(png, data.get("bbox"))
    return {"kind": "crop", "png": base64.b64encode(cropped).decode(), "caption": caption}


def crop(png, bbox):
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(type(x) not in (int, float) for x in bbox)
    ):
        raise ValueError("Missing diagram bounds")
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) or (x1 - x0) * (y1 - y0) < 0.025:
        raise ValueError("Invalid diagram bounds")
    # Cap oversized/full-slide boxes: never recreate the screenshot appendix.
    if (x1 - x0) * (y1 - y0) > 0.85:
        raise ValueError("Crop includes almost the whole slide")
    with pymupdf.open(stream=png, filetype="png") as image:
        with pymupdf.open(stream=image.convert_to_pdf(), filetype="pdf") as pdf:
            page = pdf[0]
            rect = pymupdf.Rect(
                x0 * page.rect.width,
                y0 * page.rect.height,
                x1 * page.rect.width,
                y1 * page.rect.height,
            )
            return page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(1.5, 1.5)).tobytes("png")


def needs_upgrade(markdown):
    marker = MARKER.search(markdown)
    if not marker:
        return False
    bundle = load(marker.group(1))
    return bundle is None or bundle.get("version") != VERSION or bool(bundle.get("incomplete"))


def load(key):
    from backend import cache

    if not re.fullmatch(r"[0-9a-f]{64}", key):
        return None
    data = cache.get(key)
    if data is None:
        return None
    try:
        bundle = json.loads(data)
        if not isinstance(bundle, dict) or not isinstance(bundle.get("slides"), list):
            return None
        for slide in bundle["slides"]:
            if not isinstance(slide, dict):
                return None
            if "png" in slide:
                slide["png"] = base64.b64decode(slide["png"], validate=True)
            if slide.get("kind") == "graph":
                slide["graph"] = diagrams.validate(slide["graph"])
        return bundle
    except (ValueError, KeyError, TypeError):
        return None
