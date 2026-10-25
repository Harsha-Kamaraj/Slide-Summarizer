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
