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

from backend import cache, config, llm
from backend.extract import ExtractionError
from backend.pdf_builder import build_pdf, parse_summary
from backend.summarize import summarize_file

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
@app.post("/summarize")
def summarize(
    file: UploadFile,
    mode: Literal["short", "detailed"] = Form("detailed"),
) -> Response:
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in config.ALLOWED_EXTENSIONS:
        allowed = ", ".join(config.ALLOWED_EXTENSIONS)
        raise HTTPException(415, f"Unsupported file type '{suffix}'. Allowed: {allowed}")

    start = time.perf_counter()
    # The upload only lives in a temp folder for the length of this request
    # (privacy: we never keep students' files). It keeps its original name so
    # error messages and fallback deck titles show "Lecture3.pptx", not "tmpab12".
    with tempfile.TemporaryDirectory() as tmp_dir:
        saved = Path(tmp_dir) / filename
        with saved.open("wb") as destination:
            content_hash = _save_upload(file, destination)

        key = cache.cache_key(content_hash, mode)
        with cache.lock(key):
            markdown = cache.get(key)
            cache_status = "HIT" if markdown else "MISS"
            if markdown is None:
                markdown = _summarize_file(saved, mode)
                cache.put(key, markdown)

    pdf = build_pdf(parse_summary(markdown), source_name=filename)
    logger.info(
        "Summarized %s (mode=%s, cache %s) in %.1fs",
        filename,
        mode,
        cache_status,
        time.perf_counter() - start,
    )
    download_name = f"{Path(filename).stem}_summary.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "X-Cache": cache_status,
        },
    )


def _summarize_file(path: Path, mode: str) -> str:
    try:
        return summarize_file(path, mode=mode)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except llm.LLMError as exc:
        logger.error("LLM failure for %s: %s", path.name, exc)
        raise HTTPException(503, f"The summarizer is unavailable right now: {exc}") from exc


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
