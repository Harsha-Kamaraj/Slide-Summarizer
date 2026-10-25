# Design Decisions

A running log of non-obvious choices and why they were made. Newest at the bottom.

## 1. A Python backend sits between the extension and the LLM
Parsing PPTX files, rendering slides to images and building PDFs are all mature, easy tasks in Python and awkward in browser JavaScript. Putting them in a backend keeps the extension thin (it just fetches a file and downloads a PDF). It also sidesteps Ollama's CORS rules, which block requests from `chrome-extension://` origins by default, and it means that in production the LLM API key lives on the server, never inside the extension where anyone could read it.

## 2. The extension fetches the PPT, not the backend
Lecture files on the college site sit behind a student login. The extension runs inside the student's browser, so its requests already carry their session cookies. The backend has no session and would need the student's password to log in, which we never want to handle. So the extension downloads the bytes and uploads them to the backend.

## 3. One LLM interface using the OpenAI-compatible Chat Completions format
All model calls go through `backend/llm.py`, which speaks the OpenAI Chat Completions format. Ollama serves this format at `/v1`, and so do most hosted providers. Moving from local development to a hosted API is then a configuration change (`LLM_BASE_URL`, `LLM_API_KEY`, model name), not a code change. The rest of the code never knows which provider it is talking to.

## 4. Default models for a 16 GB Apple Silicon Mac
The development machine is an M4 with 16 GB of unified memory, which is shared between the OS, the browser and the model. The default text model is `gemma4:12b` (about 7.7 GB). It is the largest model that fits comfortably and gives noticeably better summaries than 4B models. `gemma3:4b` (3.3 GB) is the fast fallback, and you can switch to it by setting `TEXT_MODEL`. Both are already pulled locally.

`gemma4:12b` also reports `vision` capability (`ollama show`), so it is the default vision model too. A single model means only one model's weights are in memory, with no reloads or swapping between a text model and a vision model, which matters on 16 GB. This will be re-checked against real image-heavy slides in Stage 5.

Note: `gemma4:12b` has "thinking" on by default. A trivial one-word reply took about 11 seconds because of it. For summarization we will turn thinking off unless the eval in Stage 6 shows it improves faithfulness enough to be worth the time.

## 5. Python 3.13 for the virtual environment
The machine has both 3.13 and 3.14. We use 3.13 because every dependency (PyMuPDF, reportlab, python-pptx) is known to ship prebuilt wheels for it, and it still meets the 3.11+ requirement. It also matches the slim Docker base images we'll use when deploying.

## 6. `backend` is a Python package, run from the repo root
The Stage 1 CLI is `python -m backend.summarize`, which requires `backend/` to be a package imported from the repo root. To keep one consistent import style (`from backend.config import ...`), the API server will also be started from the root with `uvicorn backend.main:app`, not with `cd backend && uvicorn main:app`. Mixing the two styles breaks imports in one mode or the other.

## 7. PDF is a first-class input, not an afterthought
The college site (PESU Academy, `pesuacademy.com/Academy/s/studentProfilePESU#`) lists slides under Course → Unit → **Slides** tab. Each entry (e.g. "ML models") has an eye icon that opens a viewer, and from there the student can save the deck as a PDF. Lecturers upload .pptx, .ppt and PDF, but what the browser actually receives is often a PDF. So the extractor treats all three as equal inputs: .pptx through python-pptx (which also gives us speaker notes), PDF through PyMuPDF (one page = one slide), and legacy .ppt by converting to .pptx with LibreOffice headless first.

The site is a single-page app (the URL ends in `#` and content changes without page loads), so the Stage 4 content script will need a `MutationObserver` to notice when the Slides tab renders, not just a one-time scan on page load. The exact request the eye icon makes is still to be captured from DevTools before Stage 4.

## 8. Map-reduce summarization
A 60-slide deck is too much text to summarize well in one prompt on a 12B model. The model would lose detail in the middle, and slower local models get much slower as the prompt grows. So we **map**: split the slides into chunks of `CHUNK_SIZE` (8) and turn each chunk into revision notes. Then we **reduce**: send all the chunk notes to one final prompt that merges duplicate topics and produces the finished document. Chunks of about 8 slides are big enough that a topic spanning several slides stays together, and small enough to fit any model's context window with plenty of room. The trade-off is one extra LLM call per chunk, and a topic split across a chunk boundary is reassembled in the reduce step.

## 9. Prompts are plain text files with a shared system prompt
Prompts live in `backend/prompts/*.txt`, not in Python strings, so they can be read and tweaked without touching code, and prompt changes show up clearly in git diffs. The anti-hallucination rules (use only slide content, say "not covered in slides") sit in one `system.txt` that every call shares, so they can't drift apart between steps. Templates use Python's `string.Template` (`$name`) rather than `str.format` (`{name}`), because lecture content and prompts often contain literal braces (code, set notation) that would break `format`.

## 10. The summary is Markdown with fixed headings
The final output uses fixed Markdown headings (`## Overview`, `## Key Points`, `## Important Terms`). Markdown is readable as-is in the terminal (Stage 1), small models produce it reliably, and the PDF builder (Stage 2) can turn it into sections by splitting on those known headings. JSON output was the alternative, but it is unreadable in the terminal, and small models sometimes break the syntax. Constrained decoding would fix that, but it ties us to providers that support it.
