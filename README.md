# Slide-Summarizer
Chrome extension that turns college lecture PPTs into AI-generated PDF summaries, powered by local LLMs via Ollama.

## Setup (development)
Requires Python 3.11+, [Ollama](https://ollama.com), and LibreOffice (only for legacy `.ppt` files).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # server deps + pytest/black
ollama pull gemma4:12b        # default model; see backend/config.py
```

## Usage (command line)
Run from the repo root:

```bash
.venv/bin/python -m backend.summarize path/to/deck.pptx                # detailed summary -> summary.pdf
.venv/bin/python -m backend.summarize path/to/deck.pdf --mode short -o notes.pdf
```

The Markdown summary is also printed to stdout. Supports `.pptx`, `.ppt` and `.pdf`. Settings (model, chunk size, LLM URL) can be overridden with env vars; see `backend/config.py`.

## Tests
```bash
.venv/bin/pytest
```

## API (Stage 3)
Start the backend from the repo root:

```bash
.venv/bin/uvicorn backend.main:app --reload --port 8000
```

```bash
curl localhost:8000/health
# {"status":"ok","ollama":true}

curl -F file=@path/to/deck.pdf -F mode=detailed localhost:8000/summarize -o summary.pdf
```

| Error | Status |
|---|---|
| Unsupported file type | 415 |
| File too large (`MAX_UPLOAD_MB`) | 413 |
| Corrupt / unreadable file | 422 |
| LLM server down or model missing | 503 |
| Over today's limit of new summaries (`DAILY_LIMIT`) | 429 |

The extension uses the job endpoints, which report progress:

```bash
curl -F file=@deck.pdf -F mode=short -F extras=flashcards localhost:8000/jobs   # {"job_id": "..."}
curl localhost:8000/jobs/<job_id>          # {"status": "running", "progress": "Summarizing part 2/5…", ...}
curl localhost:8000/jobs/<job_id>/pdf -o summary.pdf
```

Extras: add `-F extras=exam_questions` and/or `-F extras=flashcards` to either endpoint.

## Deployment (Stage 8)
See [docs/DEPLOY.md](docs/DEPLOY.md) (Docker image, free hosting, hosted LLM, sharing the extension) and [docs/PRIVACY.md](docs/PRIVACY.md).

## Chrome extension (Stage 4)
1. Start the backend (see above) and make sure Ollama is running.
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and select the `extension/` folder.
3. Open a course on PESU Academy → a unit → **Slides**. Each deck gets a **📝 Summarize** button; the summary PDF is saved to your Downloads.
4. The toolbar popup shows backend status, lets you choose **Detailed/Short**, and can summarize a file from your computer.

After changing extension code, click the ↻ reload icon on the extension's card in `chrome://extensions` and refresh the PESU tab.

## Image-heavy slides (Stage 5)
Slides with fewer than 15 words (diagrams, screenshots, quotes inside pictures) are rendered as images and described by a vision model (`VISION_MODEL`, default `gemma4:12b`) before summarizing. Turn it off with `VISION_ENABLED=0`; cap it with `MAX_VISION_SLIDES`.

Backend 0.6.0 places diagrams inside their matching topic, not in an appendix. A vision model reads selected source images and proposes a small graph; validated simple flows are drawn as PDF vectors. Complex notation uses a cropped source diagram. Covers, decorative cards and unmatched slides are omitted. Selection is heuristic and bounded to six candidates (`MAX_INLINE_DIAGRAMS`) by default; not every source diagram is guaranteed. `INCLUDE_DIAGRAMS=0` disables diagrams. It paces AI requests, splits oversized inputs before sending them, and caches successful intermediate responses, questions and flashcards. After a server restart, unfinished jobs require re-upload but reuse saved work; completed job downloads can be restored. See the deployment guide for quota settings and recovery limits.

## Evaluation (Stage 6)
```bash
.venv/bin/python -m eval.evaluate                         # all decks in eval/sample_decks
.venv/bin/python -m eval.benchmark eval/sample_decks/2.pdf  # compare model setups
```
`eval/evaluate.py` records time, slides, image slides described, and the % of summary claims an LLM judge finds supported by the slides (`JUDGE_MODEL`, ideally stronger than the summarizer). Numbers go to `eval/results.md`; summaries and flagged claims go to the gitignored `eval/summaries/`.

## Configuration
All settings are env vars read in `backend/config.py`, for example:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` | Ollama on localhost | Any OpenAI-compatible provider |
| `TEXT_MODEL` / `NOTES_MODEL` | `gemma4:12b` | Merge model / chunk-notes model |
| `VISION_MODEL`, `VISION_ENABLED` | `gemma4:12b`, `1` | Image-slide descriptions |
| `LLM_PARALLEL_CALLS` | `1` | Concurrent chunk calls (raise for hosted APIs) |
| `CACHE_DIR` | `.cache/summaries` | Cached summaries |

## License

Copyright (c) 2026 Harsha K. Licensed under the [MIT License](LICENSE).
