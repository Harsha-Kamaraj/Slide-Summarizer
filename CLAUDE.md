# Slide Summarizer — Chrome Extension

## What this project is
A Chrome extension that lets students on our college website pick a lecture PPT and get an AI-generated summary of it as a downloadable PDF. Everything runs locally and free: a local Python backend does the work, and a local LLM served by Ollama writes the summaries.

This is a learning + resume project. The owner (Harsha) must be able to explain every design decision in an interview, so:
- Prefer simple, readable code over clever code.
- Add short comments explaining *why* something exists, not just what it does.
- When you make a non-obvious choice (library, prompt design, chunking strategy), note it in `docs/DECISIONS.md` with a one-paragraph reason.
- Build in small stages and stop after each stage so it can be tested and understood before moving on.

## Context
- A senior already built a "merge slides" extension for the same college website, so fetching PPTs from the site is known to be possible. Ask Harsha for the site's URL structure and how PPT links appear on the page before building the content script — do not guess.
- Development machine: Mac (Apple Silicon). Ask Harsha for RAM size before choosing default model sizes.
- Development uses Ollama on `localhost:11434` (free, local). But the end goal is **real students using it** without installing Python or Ollama — see "Real-world deployment" below. Design for that from day one.

## Architecture

```
[College website page]
        │  content script finds PPT links, adds a "Summarize" button
        ▼
[Chrome extension (Manifest V3)]
        │  fetches the PPT file itself (so the student's login cookies apply),
        │  then POSTs the file bytes to the local backend
        ▼
[Local backend — Python + FastAPI on localhost:8000]
        │  1. extract text per slide
        │  2. render slides to images (for image-heavy slides)
        │  3. call Ollama to summarize
        │  4. build the PDF
        ▼
[Ollama on localhost:11434]  → returns summaries
        │
        ▼
[Extension] receives the PDF and triggers a download
```

Why a backend instead of calling Ollama straight from the extension: PPT parsing, slide rendering and PDF generation are far easier in Python, and it keeps the extension thin. It also avoids Ollama's CORS restrictions on `chrome-extension://` origins.

Why the extension fetches the file (not the backend): college pages usually need the student to be logged in; the extension already has that session, the backend doesn't.

## Tech stack
- **Extension:** Manifest V3, plain JavaScript (no framework), `content_script`, `background` service worker, popup HTML.
- **Backend:** Python 3.11+, FastAPI, Uvicorn.
- **Slide parsing:** `python-pptx` for .pptx text and speaker notes; `PyMuPDF` (fitz) if the file is a PDF. For legacy `.ppt`, convert with LibreOffice headless (`soffice --convert-to pptx`).
- **Slide images:** LibreOffice headless → PDF → PyMuPDF to render each page as PNG.
- **LLM:** Ollama via its HTTP API (`/api/chat`). Use the `ollama` Python package or `httpx`.
  - Text model: a small general model (e.g. a Gemma or Qwen in the 4B–12B range).
  - Vision model: a vision-capable Qwen or Gemma variant, used only for slides with little extractable text.
  - Model names must come from `backend/config.py` / env vars, never hardcoded in logic.
  - **Pluggable provider:** all LLM calls go through one interface in `backend/llm.py` using the OpenAI-compatible Chat Completions format. Ollama exposes this format, and so do most hosted providers, so switching from local Ollama to a hosted API is just a change of `LLM_BASE_URL`, `LLM_API_KEY` and model name in env vars.
- **PDF output:** `reportlab` (or `weasyprint` from HTML if layout gets complex — record the choice in DECISIONS.md).
- **Testing:** `pytest` for the backend.

## Project structure
```
slide-summarizer/
├── CLAUDE.md
├── README.md
├── docs/
│   └── DECISIONS.md
├── extension/
│   ├── manifest.json
│   ├── content.js        # finds PPT links on college pages, injects buttons
│   ├── background.js     # fetches file, calls backend, triggers download
│   ├── popup.html
│   ├── popup.js          # options: summary length, include exam questions
│   └── icons/
├── backend/
│   ├── main.py           # FastAPI app and routes
│   ├── config.py         # model names, Ollama URL, limits
│   ├── extract.py        # per-slide text + notes extraction
│   ├── render.py         # slides → PNG images
│   ├── summarize.py      # prompts + Ollama calls
│   ├── pdf_builder.py    # summary → PDF
│   ├── prompts/          # prompt templates as .txt files
│   └── requirements.txt
├── eval/
│   ├── sample_decks/     # a few real lecture PPTs for testing
│   └── evaluate.py       # scoring script (see Stage 6)
└── tests/
```

## Backend API
- `GET /health` → `{ "status": "ok", "ollama": true/false }` (checks Ollama is reachable).
- `POST /summarize` — multipart upload.
  - Fields: `file` (pptx/ppt/pdf), `mode` (`short` | `detailed`), `extras` (optional list: `exam_questions`, `flashcards`).
  - Returns: `application/pdf`.
- Max upload size and allowed types set in `config.py`. Reject anything else with a clear error.

## Summarization approach
1. Extract text + speaker notes for every slide.
2. A slide is "image-heavy" if it has fewer than ~15 words of text. Only those go to the vision model, to keep things fast.
3. Summarize in chunks of slides (e.g. 8–10 at a time) so input fits the model's context, then do one final pass that merges chunk summaries into a coherent document. (Map-reduce — explain this in DECISIONS.md.)
4. Output structure for the PDF:
   - Title of the deck
   - 3–5 line overview
   - Section-wise key points (grouped by topic, not one bullet per slide)
   - Important terms with one-line definitions
   - Optional: likely exam questions / flashcards
5. Prompts live in `backend/prompts/*.txt`. Tell the model to only use information from the slides and to say "not covered in slides" rather than invent content.

## Build stages (do these in order, stop after each)
1. **Script first.** `backend/extract.py` + `summarize.py` as a CLI: `python -m backend.summarize path/to/deck.pptx` prints a summary to the terminal. No web, no extension.
2. **PDF output.** Add `pdf_builder.py`; CLI now writes `summary.pdf`.
3. **Backend API.** Wrap it in FastAPI with `/health` and `/summarize`. Test with `curl`.
4. **Extension.** Popup with a file picker that uploads to the backend and downloads the PDF. Then the content script that adds "Summarize" buttons next to PPT links on the college site.
5. **Vision for image-heavy slides.** Add `render.py` and the vision-model path.
6. **Evaluation.** In `eval/`, run 5–10 real decks; for each, record time taken, slides processed, and a simple quality check (e.g. ask the LLM to verify each summary bullet is supported by the slide text, report % supported). Save results to `eval/results.md`. These numbers go on the resume.
7. **Extras.** Exam questions, flashcards, then (optional) "chat with this PPT" using an Ollama embedding model + simple vector search (RAG).
8. **Real-world deployment.** Make it usable by any student with just the extension installed (details below).

## Real-world deployment
Students will not install Python, LibreOffice or Ollama, so for real use:
- **Hosted backend:** containerize the backend with a `Dockerfile` (include LibreOffice) and deploy it to a cloud host. Backend URL comes from extension settings / a config constant, defaulting to the hosted URL, with `localhost:8000` for development.
- **Hosted LLM:** in production, point `llm.py` at a hosted OpenAI-compatible API (free tier where possible). Keep Ollama for local dev. API keys live only on the server as env vars, never in the extension.
- **Abuse and cost protection:** rate limit per user/IP (e.g. N summaries per day), max file size, max slide count, request timeout.
- **Caching:** hash each uploaded file (SHA-256); if the same deck was summarized before with the same options, return the cached PDF. Many students in one class summarize the same PPT, so this saves most of the cost.
- **Speed and feedback:** summaries can take time, so show progress in the extension (e.g. "Extracting slides… Summarizing 3/5…") via a job ID + polling endpoint (`POST /jobs`, `GET /jobs/{id}`), instead of one long blocking request.
- **Privacy:** do not store uploaded files after processing (only the cached summary). Write a short privacy policy page — required for the Chrome Web Store. Get the college's okay before publishing.
- **Reliability:** clear error messages for students (not stack traces), basic logging, a `/health` check, and retries on LLM timeouts.
- **Distribution:** publish to the Chrome Web Store (needs a developer account, icons, screenshots, description, privacy policy). Until then, share as an unpacked extension with install instructions in README.
- **Feedback loop:** a 👍/👎 button on each summary sent to the backend, so real usage data can improve prompts and be quoted on the resume (e.g. "used by X students, Y% positive").

## Conventions
- Python: type hints, small functions, `black` formatting, `logging` instead of `print` in the backend.
- JS: plain ES modules, no build step, `async/await`.
- Never commit sample lecture files that the college may not want public; keep `eval/sample_decks/` in `.gitignore`.
- Handle failures gracefully: Ollama not running, file too large, corrupt PPT, model missing (tell the user which `ollama pull` command to run).

## Commands
- Install Ollama models: `ollama pull <model>` (names from `config.py`)
- Backend: `cd backend && pip install -r requirements.txt && uvicorn main:app --reload --port 8000`
- Tests: `pytest`
- Load the extension: Chrome → `chrome://extensions` → Developer mode → Load unpacked → select `extension/`

## Before starting, ask Harsha
1. The college website URL and what a page with PPT links looks like (or a saved HTML copy).
2. Whether files are .pptx, .ppt, PDF, or a mix.
3. Mac RAM size (to pick model sizes).
4. Whether LibreOffice is installed (`brew install --cask libreoffice` if not).
