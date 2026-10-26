"""Keep selected original visual slides, never AI-redrawn diagrams."""

import base64
import hashlib
import json
import re
from pathlib import Path

from backend import config
from backend.render import render_slides

MARKER = re.compile(r"<!-- source-figures:([0-9a-f]{64}) -->")


def prepare(deck, path, report):
    from backend import cache

    candidates = [s for s in deck.slides if s.has_visual and not s.is_administrative]
    limit = max(0, config.MAX_DIAGRAM_SLIDES)
    targets = candidates
    if len(candidates) > limit and limit:
        # Cover the entire lecture when capped, rather than dropping all late topics.
        targets = [
            candidates[round(i * (len(candidates) - 1) / max(1, limit - 1))] for i in range(limit)
        ]
    if limit == 0:
        return ""
    if not config.INCLUDE_DIAGRAMS or not targets:
        return ""
    with Path(path).open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    captions = {}
    for slide in targets:
        caption = " ".join(slide.text.split())
        if len(caption) > 400:
            caption = caption[:400].rsplit(" ", 1)[0] + "…"
        captions[slide.number] = caption
    key = cache.object_key("source-figures-v3", [digest, captions])
    if cache.get(key) is None:
        report(f"Saving {len(targets)} original visual slides…")
        images = render_slides(path, [s.number for s in targets])
        selected = []
        for slide in targets:
            if slide.number in images:
                selected.append(
                    {
                        "number": slide.number,
                        "title": slide.title,
                        "caption": captions[slide.number],
                        "png": base64.b64encode(images[slide.number]).decode(),
                    }
                )
        cache.put(
            key,
            json.dumps(
                {
                    "total": len(candidates),
                    "slides": selected,
                }
            ),
        )
    return f"<!-- source-figures:{key} -->"


def load(key):
    from backend import cache

    if not re.fullmatch(r"[0-9a-f]{64}", key):
        return None
    data = cache.get(key)
    if data is None:
        return None
    try:
        bundle = json.loads(data)
        for slide in bundle["slides"]:
            slide["png"] = base64.b64decode(slide["png"], validate=True)
        return bundle
    except (ValueError, KeyError, TypeError):
        return None
