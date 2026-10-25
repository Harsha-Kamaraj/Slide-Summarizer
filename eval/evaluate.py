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
from backend.extract import Deck, Slide, extract_deck
from backend.pdf_builder import parse_summary
from backend.summarize import format_slides, summarize_deck
from backend.vision import describe_image_slides

DECKS_DIR = Path("eval/sample_decks")
SUMMARIES_DIR = Path("eval/summaries")
RESULTS_FILE = Path("eval/results.md")
JUDGE_PROMPT = Template((Path(__file__).parent / "prompts" / "judge_claims.txt").read_text())
COVERAGE_PROMPT = Template((Path(__file__).parent / "prompts" / "judge_coverage.txt").read_text())
# Slides per coverage call: each call also carries the whole summary.
SLIDES_PER_COVERAGE_CALL = 6
COVERAGE_LINE = re.compile(r"^\s*(\d+)\s*\|\s*(COVERED|PARTIAL|MISSING|ADMIN)\b", re.I)

# Claims per judge call, and slides shown per claim. The judge sees only the
# slides most likely to support each claim (by shared words), not the whole
# deck: a 58-slide deck plus image descriptions is ~7k tokens, more than a free
# hosted tier allows per minute. Trade-off: if the right slide isn't among the
# top few, a true claim is marked unsupported, so scores err on the strict side.
CLAIMS_PER_CALL = 5
SLIDES_PER_CLAIM = 3
VERDICT_LINE = re.compile(r"^\s*(\d+)\s*\|\s*(SUPPORTED|PARTIAL|UNSUPPORTED)\s*\|\s*(.*)$", re.I)


def extract_claims(markdown: str) -> list[str]:
    """Every checkable statement in the summary: bullets and numbered steps,
    formulas, and term definitions. (Headings and the overview are skipped:
    they restate the bullets.)"""
    summary = parse_summary(markdown)
    claims = [point for topic in summary.topics for point in topic.points]
    claims += [f"{term}: {definition}" for term, definition in summary.terms if definition]
    return [claim for claim in claims if _is_statement(claim)]


def _is_statement(claim: str) -> bool:
    """Skip fragments that state nothing checkable: a label introducing the
    next lines ("Steps:"), an empty formula, a lone number, and the honest
    placeholders the prompts ask for ("Solution not given in slides"), which
    a judge would otherwise wrongly count as unsupported."""
    if claim.startswith("Table: "):
        return False  # a whole table isn't one checkable statement
    text = re.sub(r"^(Formula:|\d+\.)\s*", "", claim).strip()
    if re.search(r"not (given|covered) in (the )?slides", text, re.IGNORECASE):
        return False
    return len(text.split()) >= 3 and not text.endswith(":")


def judge_claims(deck: Deck, claims: list[str]) -> list[str]:
    """Return one verdict per claim: SUPPORTED, PARTIAL, UNSUPPORTED or UNKNOWN
    (the judge's reply for that claim couldn't be parsed)."""
    verdicts: list[str] = []
    for start in range(0, len(claims), CLAIMS_PER_CALL):
        batch = claims[start : start + CLAIMS_PER_CALL]
        numbered = "\n".join(f"{i}. {claim}" for i, claim in enumerate(batch, start=1))
        slides = format_slides(relevant_slides(deck, batch))
        reply = llm.chat(
            [{"role": "user", "content": JUDGE_PROMPT.substitute(slides=slides, claims=numbered)}],
            model=config.JUDGE_MODEL,
            temperature=0,
        )
        verdicts += parse_verdicts(reply, len(batch))
    return verdicts


_WORD = re.compile(r"[a-z0-9]+")
# Words too common to say which slide a claim came from.
_STOPWORDS = set(
    "the a an and or of to in on for is are be by with as that this it its from at which "
    "can each into using used use their they them than then these those has have not".split()
)


def relevant_slides(deck: Deck, claims: list[str]) -> list[Slide]:
    """The slides sharing the most words with each claim, in deck order."""

    def words(text: str) -> set[str]:
        return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}

    slide_words = [
        (slide, words(f"{slide.title} {slide.text} {slide.notes}")) for slide in deck.slides
    ]
    chosen: set[int] = set()
    for claim in claims:
        claim_words = words(claim)
        ranked = sorted(slide_words, key=lambda sw: len(claim_words & sw[1]), reverse=True)
        chosen.update(slide.number for slide, _ in ranked[:SLIDES_PER_CLAIM])
    return [slide for slide in deck.slides if slide.number in chosen]


def judge_coverage(deck: Deck, summary: str) -> dict[int, str]:
    """Faithfulness asks "is what's written true?"; this asks the other half,
    "is everything important included?". Returns {slide number: verdict}."""
    verdicts: dict[int, str] = {}
    slides = deck.slides
    for start in range(0, len(slides), SLIDES_PER_COVERAGE_CALL):
        batch = slides[start : start + SLIDES_PER_COVERAGE_CALL]
        reply = llm.chat(
            [
                {
                    "role": "user",
                    "content": COVERAGE_PROMPT.substitute(
                        summary=summary, slides=format_slides(batch)
                    ),
                }
            ],
            model=config.JUDGE_MODEL,
            temperature=0,
        )
        found = {
            int(m.group(1)): m.group(2).upper()
            for m in map(COVERAGE_LINE.match, reply.splitlines())
            if m
        }
        for slide in batch:
            verdicts[slide.number] = found.get(slide.number, "UNKNOWN")
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
    coverage = judge_coverage(deck, markdown)
    teaching = [n for n, v in coverage.items() if v in ("COVERED", "PARTIAL", "MISSING")]
    missing = [n for n in teaching if coverage[n] == "MISSING"]

    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    (SUMMARIES_DIR / f"eval_{path.stem}_{mode}.md").write_text(markdown)
    flagged = [f"- [{v}] {c}" for c, v in zip(claims, verdicts) if v != "SUPPORTED"]
    flagged += [f"- [{coverage[n]} slide] {n}" for n in teaching if coverage[n] != "COVERED"]
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
        "covered_%": _percent(sum(coverage[n] == "COVERED" for n in teaching), len(teaching)),
        "partly_covered_%": _percent(
            sum(coverage[n] == "PARTIAL" for n in teaching), len(teaching)
        ),
        "missing_slides": ",".join(map(str, missing)) or "-",
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
        f"\n## {date.today()} · mode={args.mode} · {config.LLM_BASE_URL}\n",
        f"notes={config.NOTES_MODEL} · merge={config.TEXT_MODEL} · "
        f"vision={config.VISION_MODEL if config.VISION_ENABLED else 'off'} · "
        f"judge={config.JUDGE_MODEL}\n",
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
        *("| " + " | ".join(str(row[c]) for c in columns) + " |" for row in rows),
    ]
    with RESULTS_FILE.open("a") as f:
        f.write("\n".join(table) + "\n")
    print("\n".join(table))


if __name__ == "__main__":
    main()
