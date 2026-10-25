# Slide-Summarizer
Chrome extension that turns college lecture PPTs into AI-generated PDF summaries, powered by local LLMs via Ollama.

## Setup (development)
Requires Python 3.11+, [Ollama](https://ollama.com), and LibreOffice (only for legacy `.ppt` files).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
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
