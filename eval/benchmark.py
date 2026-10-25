"""Compare model setups on one deck: speed, token usage and basic quality signals.

Usage (from the repo root):
    .venv/bin/python -m eval.benchmark eval/sample_decks/2.pdf
    .venv/bin/python -m eval.benchmark eval/sample_decks/2.pdf --only baseline-12b notes-4b

Each setup's summary is saved to eval/summaries/bench_<name>.md (gitignored,
it contains lecture content) so quality can be compared by reading them.
Timing table is appended to eval/benchmark_results.md.
"""

import argparse
import logging
import time
from datetime import date
from pathlib import Path

from backend import config, llm
from backend.extract import extract_deck
from backend.pdf_builder import parse_summary
from backend.summarize import structure_problem, summarize_deck

# name -> config overrides. Every setup uses the same prompts and mode.
SETUPS: dict[str, dict] = {
    "baseline-12b": {"NOTES_MODEL": "gemma4:12b", "TEXT_MODEL": "gemma4:12b"},
    "parallel2-12b": {
        "NOTES_MODEL": "gemma4:12b",
        "TEXT_MODEL": "gemma4:12b",
        "LLM_PARALLEL_CALLS": 2,
    },
    "notes-4b": {"NOTES_MODEL": "gemma3:4b", "TEXT_MODEL": "gemma4:12b"},
    "notes-llama8b": {"NOTES_MODEL": "llama3.1:8b", "TEXT_MODEL": "gemma4:12b"},
}

SUMMARIES_DIR = Path("eval/summaries")
RESULTS_FILE = Path("eval/benchmark_results.md")


class TokenCounter:
    """Wrap the LLM call to add up tokens, without changing backend code."""

    def __init__(self):
        self.calls = self.prompt_tokens = self.completion_tokens = 0
        self._original = llm._post_with_retries

    def __enter__(self):
        def counting(payload, model):
            data = self._original(payload, model)
            usage = data.get("usage") or {}
            self.calls += 1
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)
            return data

        llm._post_with_retries = counting
        return self

    def __exit__(self, *exc):
        llm._post_with_retries = self._original


def warm_up(models: set[str]) -> None:
    # Load each model into memory first, so timings measure summarizing, not
    # the one-off disk load (a hosted API never has that cost).
    for model in models:
        llm.chat([{"role": "user", "content": "Reply with: ok"}], model=model)


def run_setup(name: str, overrides: dict, deck_path: str, mode: str) -> dict:
    for key, value in overrides.items():
        setattr(config, key, value)
    warm_up({config.NOTES_MODEL, config.TEXT_MODEL})

    deck = extract_deck(deck_path)
    start = time.perf_counter()
    with TokenCounter() as tokens:
        markdown = summarize_deck(deck, mode=mode)
    seconds = time.perf_counter() - start

    (SUMMARIES_DIR / f"bench_{name}.md").write_text(markdown)
    parsed = parse_summary(markdown)
    return {
        "setup": name,
        "seconds": round(seconds),
        "calls": tokens.calls,
        "tokens_in": tokens.prompt_tokens,
        "tokens_out": tokens.completion_tokens,
        "out_tok_per_s": round(tokens.completion_tokens / seconds, 1),
        "words": len(markdown.split()),
        "topics": len(parsed.topics),
        "formulas": sum(p.startswith("Formula: ") for t in parsed.topics for p in t.points),
        "structure_ok": structure_problem(markdown) is None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("deck")
    parser.add_argument("--mode", default="detailed")
    parser.add_argument("--only", nargs="+", choices=list(SETUPS), help="setups to run")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)

    defaults = {key: getattr(config, key) for s in SETUPS.values() for key in s}
    rows = []
    for name in args.only or SETUPS:
        logging.info("=== %s", name)
        rows.append(run_setup(name, SETUPS[name], args.deck, args.mode))
        logging.info("%s", rows[-1])
        for key, value in defaults.items():  # reset before the next setup
            setattr(config, key, value)

    columns = list(rows[0])
    table = [
        f"\n## {date.today()} · {Path(args.deck).name} · mode={args.mode} · {config.LLM_BASE_URL}\n",
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
        *("| " + " | ".join(str(row[c]) for c in columns) + " |" for row in rows),
    ]
    with RESULTS_FILE.open("a") as f:
        f.write("\n".join(table) + "\n")
    print("\n".join(table))


if __name__ == "__main__":
    main()
