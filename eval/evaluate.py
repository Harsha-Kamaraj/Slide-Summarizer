"""Stage 6: run real decks end to end and measure speed and faithfulness.

Usage (from the repo root):
    .venv/bin/python -m eval.evaluate                      # every deck in eval/sample_decks
    .venv/bin/python -m eval.evaluate eval/sample_decks/2.pdf --mode short

For each deck: time taken, slides, image slides described, and the share of
summary claims (bullets, formulas, term definitions) that an LLM judge finds
supported by the slide text. Numbers are appended to eval/results.md (safe to
commit); the summaries and the unsupported claims, which contain lecture
content, go to eval/summaries/ (gitignored).
"""

import argparse
import logging
import re
import time
from datetime import date
from pathlib import Path
from string import Template

from backend import config, llm
from backend.extract import Deck, extract_deck
from backend.pdf_builder import parse_summary
from backend.summarize import format_slides, summarize_deck
from backend.vision import describe_image_slides

DECKS_DIR = Path("eval/sample_decks")
SUMMARIES_DIR = Path("eval/summaries")
RESULTS_FILE = Path("eval/results.md")
JUDGE_PROMPT = Template((Path(__file__).parent / "prompts" / "judge_claims.txt").read_text())

# Claims per judge call: few enough that the model checks each one carefully,
# many enough to keep the number of calls (and minutes) down.
CLAIMS_PER_CALL = 15
VERDICT_LINE = re.compile(r"^\s*(\d+)\s*\|\s*(SUPPORTED|PARTIAL|UNSUPPORTED)\s*\|\s*(.*)$", re.I)


def extract_claims(markdown: str) -> list[str]:
    """Every checkable statement in the summary: bullets and numbered steps,
    formulas, and term definitions. (Headings and the overview are skipped:
    they restate the bullets.)"""
    summary = parse_summary(markdown)
    claims = [point for topic in summary.topics for point in topic.points]
    claims += [f"{term}: {definition}" for term, definition in summary.terms if definition]
    return claims


def judge_claims(deck: Deck, claims: list[str]) -> list[str]:
    """Return one verdict per claim: SUPPORTED, PARTIAL, UNSUPPORTED or UNKNOWN
    (the judge's reply for that claim couldn't be parsed)."""
    slides = format_slides(deck.slides)
    verdicts: list[str] = []
    for start in range(0, len(claims), CLAIMS_PER_CALL):
        batch = claims[start : start + CLAIMS_PER_CALL]
        numbered = "\n".join(f"{i}. {claim}" for i, claim in enumerate(batch, start=1))
        reply = llm.chat(
            [{"role": "user", "content": JUDGE_PROMPT.substitute(slides=slides, claims=numbered)}],
            model=config.JUDGE_MODEL,
            temperature=0,
        )
        verdicts += parse_verdicts(reply, len(batch))
    return verdicts


def parse_verdicts(reply: str, count: int) -> list[str]:
    found: dict[int, str] = {}
    for line in reply.splitlines():
        match = VERDICT_LINE.match(line)
        if match:
            found[int(match.group(1))] = match.group(2).upper()
    return [found.get(i, "UNKNOWN") for i in range(1, count + 1)]


def evaluate_deck(path: Path, mode: str) -> dict:
    logging.info("=== %s", path.name)
    start = time.perf_counter()
    deck = extract_deck(path)
    image_slides = sum(s.is_image_heavy for s in deck.slides)
    described = describe_image_slides(deck, path) if config.VISION_ENABLED else 0
    markdown = summarize_deck(deck, mode=mode)
    seconds = time.perf_counter() - start

    # Judge against the same slide text the summarizer saw (including image
    # descriptions), so this measures the summarizer's faithfulness.
    claims = extract_claims(markdown)
    verdicts = judge_claims(deck, claims)
    counts = {v: verdicts.count(v) for v in ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "UNKNOWN")}

    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    (SUMMARIES_DIR / f"eval_{path.stem}_{mode}.md").write_text(markdown)
    flagged = [f"- [{v}] {c}" for c, v in zip(claims, verdicts) if v != "SUPPORTED"]
    (SUMMARIES_DIR / f"eval_{path.stem}_{mode}_flagged.md").write_text("\n".join(flagged) + "\n")

    judged = len(claims) - counts["UNKNOWN"]
    return {
        "deck": path.name,
        "slides": len(deck.slides),
        "image_slides": f"{described}/{image_slides}",
        "seconds": round(seconds),
        "words": len(markdown.split()),
        "claims": len(claims),
        "supported_%": _percent(counts["SUPPORTED"], judged),
        "partial_%": _percent(counts["PARTIAL"], judged),
        "unsupported_%": _percent(counts["UNSUPPORTED"], judged),
        "unparsed": counts["UNKNOWN"],
    }


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}" if whole else "-"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("decks", nargs="*", type=Path, help="default: all of eval/sample_decks")
    parser.add_argument("--mode", default="detailed", choices=["short", "detailed"])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    decks = args.decks or sorted(
        p for p in DECKS_DIR.iterdir() if p.suffix.lower() in config.ALLOWED_EXTENSIONS
    )
    rows = [evaluate_deck(path, args.mode) for path in decks]

    columns = list(rows[0])
    table = [
        f"\n## {date.today()} · mode={args.mode} · summarizer={config.TEXT_MODEL} · "
        f"judge={config.JUDGE_MODEL} · vision={'on' if config.VISION_ENABLED else 'off'}\n",
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
        *("| " + " | ".join(str(row[c]) for c in columns) + " |" for row in rows),
    ]
    with RESULTS_FILE.open("a") as f:
        f.write("\n".join(table) + "\n")
    print("\n".join(table))


if __name__ == "__main__":
    main()
