"""HTTP API around the summarizer.

Run from the repo root:  .venv/bin/uvicorn backend.main:app --reload --port 8000
"""

import hashlib
import logging
import tempfile
import time
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import Response

from backend import config, llm, service
from backend.extract import ExtractionError
from backend.pdf_builder import build_pdf, parse_summary

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="Slide Summarizer")

# Read uploads in 1 MB pieces so an oversized file is rejected as soon as it
# crosses the limit, instead of being loaded into memory in full first.
_READ_CHUNK = 1024 * 1024


@app.get("/health")
def health() -> dict:
    """Is the API up, and can it reach the LLM server?"""
    return {"status": "ok", "ollama": _llm_reachable()}


def _llm_reachable() -> bool:
    # /models is part of the OpenAI-compatible API, so this works for Ollama
    # and hosted providers alike, and it's cheap (no generation).
    try:
        response = httpx.get(
            f"{config.LLM_BASE_URL.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            timeout=3,
        )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


# A plain `def` (not `async def`) on purpose: summarizing blocks for minutes
# while waiting on the model. FastAPI runs sync endpoints in a worker thread,
# so other requests (like /health) keep being served meanwhile.
Mode = Literal["short", "detailed"]
Extra = Literal["exam_questions", "flashcards"]


@app.post("/summarize")
def summarize(
    file: UploadFile,
    mode: Mode = Form("detailed"),
    extras: list[Extra] = Form([]),
) -> Response:
    filename = _check_filename(file)
    start = time.perf_counter()
    # The upload only lives in a temp folder for the length of this request
    # (privacy: we never keep students' files). It keeps its original name so
    # error messages and fallback deck titles show "Lecture3.pptx", not "tmpab12".
    with tempfile.TemporaryDirectory() as tmp_dir:
        saved = Path(tmp_dir) / filename
        with saved.open("wb") as destination:
            content_hash = _save_upload(file, destination)
        markdown, from_cache = _run_pipeline(saved, content_hash, mode, extras)

    cache_status = "HIT" if from_cache else "MISS"
    logger.info(
        "Summarized %s (mode=%s, extras=%s, cache %s) in %.1fs",
        filename,
        mode,
        ",".join(extras) or "none",
        cache_status,
        time.perf_counter() - start,
    )
    return _pdf_response(markdown, filename, cache_status)


def _check_filename(file: UploadFile) -> str:
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in config.ALLOWED_EXTENSIONS:
        allowed = ", ".join(config.ALLOWED_EXTENSIONS)
        raise HTTPException(415, f"Unsupported file type '{suffix}'. Allowed: {allowed}")
    return filename


def _run_pipeline(path: Path, content_hash: str, mode: str, extras: list[str], progress=None):
    # dict.fromkeys drops duplicates but keeps the order the student chose.
    extras = list(dict.fromkeys(extras))
    try:
        return service.run(path, content_hash, mode, extras, progress or service.no_progress)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except llm.LLMError as exc:
        logger.error("LLM failure for %s: %s", path.name, exc)
        raise HTTPException(503, f"The summarizer is unavailable right now: {exc}") from exc


def _pdf_response(markdown: str, filename: str, cache_status: str) -> Response:
    pdf = build_pdf(parse_summary(markdown), source_name=filename)
    download_name = f"{Path(filename).stem}_summary.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "X-Cache": cache_status,
        },
    )


def _save_upload(upload: UploadFile, destination) -> str:
    """Write the upload to disk and return its SHA-256 (the cache fingerprint),
    computed on the way through so the file is only read once."""
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    digest = hashlib.sha256()
    while piece := upload.file.read(_READ_CHUNK):
        size += len(piece)
        if size > limit:
            raise HTTPException(413, f"File is larger than the {config.MAX_UPLOAD_MB} MB limit.")
        destination.write(piece)
        digest.update(piece)
    if size == 0:
        raise HTTPException(400, "The uploaded file is empty.")
    return digest.hexdigest()
