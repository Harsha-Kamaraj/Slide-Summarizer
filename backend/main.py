"""HTTP API around the summarizer.

Run from the repo root:  .venv/bin/uvicorn backend.main:app --reload --port 8000

Two ways to summarize:
  POST /summarize       one request that returns the PDF (simple; for curl/CLI)
  POST /jobs            returns a job id at once; poll GET /jobs/{id} for
                        progress, then GET /jobs/{id}/pdf (used by the extension)
"""

import hashlib
import json
import logging
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend import config, jobs, llm, ratelimit, service
from backend.extract import ExtractionError
from backend.pdf_builder import build_pdf, parse_summary

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="Slide Summarizer")

Mode = Literal["short", "detailed"]
Extra = Literal["exam_questions", "flashcards"]

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


# --- One-shot summarize ---------------------------------------------------


# A plain `def` (not `async def`) on purpose: summarizing blocks for minutes
# while waiting on the model. FastAPI runs sync endpoints in a worker thread,
# so other requests (like /health) keep being served meanwhile.
@app.post("/summarize")
def summarize(
    request: Request,
    file: UploadFile,
    mode: Mode = Form("detailed"),
    extras: list[Extra] = Form([]),
) -> Response:
    filename = _check_filename(file)
    extras = _unique(extras)
    start = time.perf_counter()
    # The upload only lives in a temp folder for the length of this request
    # (privacy: we never keep students' files). It keeps its original name so
    # error messages and fallback deck titles show "Lecture3.pptx", not "tmpab12".
    with tempfile.TemporaryDirectory() as tmp_dir:
        saved = Path(tmp_dir) / filename
        with saved.open("wb") as destination:
            content_hash = _save_upload(file, destination)
        _enforce_daily_limit(request, content_hash, mode, extras)
        try:
            markdown, from_cache = service.run(saved, content_hash, mode, extras)
        except ExtractionError as exc:
            raise HTTPException(422, str(exc)) from exc
        except llm.LLMError as exc:
            logger.error("LLM failure for %s: %s", filename, exc)
            raise HTTPException(503, f"The summarizer is unavailable right now: {exc}") from exc

    cache_status = "HIT" if from_cache else "MISS"
    logger.info(
        "Summarized %s (mode=%s, extras=%s, cache %s) in %.1fs",
        filename,
        mode,
        ",".join(extras) or "none",
        cache_status,
        time.perf_counter() - start,
    )
    return _pdf_response(markdown, filename, cache_status, service.summary_id(content_hash, mode))


# --- Jobs (queue + progress) ----------------------------------------------


@app.post("/jobs", status_code=202)
def create_job(
    request: Request,
    file: UploadFile,
    mode: Mode = Form("detailed"),
    extras: list[Extra] = Form([]),
) -> dict:
    filename = _check_filename(file)
    extras = _unique(extras)
    # The job's worker deletes this folder when it finishes; until the job is
    # queued, we're responsible for deleting it on any error.
    upload_dir = jobs.new_upload_dir()
    try:
        saved = upload_dir / filename
        with saved.open("wb") as destination:
            content_hash = _save_upload(file, destination)
        _enforce_daily_limit(request, content_hash, mode, extras)
        job = jobs.submit(upload_dir, saved, content_hash, mode, extras)
    except BaseException:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise
    logger.info("Queued job %s for %s (mode=%s)", job.id, filename, mode)
    return {"job_id": job.id, "status": job.status}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = _get_job(job_id)
    return {
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "cached": job.from_cache,
        "summary_id": job.summary_id,
    }


@app.get("/jobs/{job_id}/pdf")
def job_pdf(job_id: str) -> Response:
    job = _get_job(job_id)
    if job.status != "done":
        raise HTTPException(409, f"The summary isn't ready yet ({job.status}).")
    return _pdf_response(
        job.markdown, job.filename, "HIT" if job.from_cache else "MISS", job.summary_id
    )


def _get_job(job_id: str) -> jobs.Job:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown or expired job. Please summarize again.")
    return job


# --- Feedback ---------------------------------------------------------------


class Feedback(BaseModel):
    summary_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    rating: Literal["up", "down"]
    comment: str = Field("", max_length=500)


_feedback_lock = threading.Lock()


@app.post("/feedback", status_code=204)
def feedback(item: Feedback) -> None:
    """Store a 👍/👎 for a summary. No IP or user info is kept: just which
    summary, the rating and an optional comment."""
    record = {"time": datetime.now(timezone.utc).isoformat(), **item.model_dump()}
    path = Path(config.FEEDBACK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _feedback_lock, path.open("a") as f:
        f.write(json.dumps(record) + "\n")


@app.get("/feedback/stats")
def feedback_stats() -> dict:
    """Totals for the README/resume ("Y% positive")."""
    path = Path(config.FEEDBACK_FILE)
    ratings = [json.loads(line)["rating"] for line in path.open()] if path.exists() else []
    up, down = ratings.count("up"), ratings.count("down")
    return {
        "up": up,
        "down": down,
        "positive_%": round(100 * up / len(ratings)) if ratings else None,
    }


# --- Helpers ----------------------------------------------------------------


def _check_filename(file: UploadFile) -> str:
    # Only the last path component: a crafted name like "../../x.pdf" can't
    # escape the temp folder.
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in config.ALLOWED_EXTENSIONS:
        allowed = ", ".join(config.ALLOWED_EXTENSIONS)
        raise HTTPException(415, f"Unsupported file type '{suffix}'. Allowed: {allowed}")
    return filename


def _unique(extras: list[str]) -> list[str]:
    # Drop duplicates but keep the order the student chose.
    return list(dict.fromkeys(extras))


def _enforce_daily_limit(request: Request, content_hash: str, mode: str, extras: list[str]) -> None:
    # Cached summaries are free, so only new work counts against the limit.
    if service.is_cached(content_hash, mode, extras):
        return
    if not ratelimit.allow(_client_id(request)):
        raise HTTPException(
            429,
            f"You've reached today's limit of {config.DAILY_LIMIT} new summaries. "
            "Summaries others have already made still work; try new decks tomorrow.",
        )


def _client_id(request: Request) -> str:
    if config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()  # the original client
    return request.client.host if request.client else "unknown"


def _pdf_response(markdown: str, filename: str, cache_status: str, summary_id: str) -> Response:
    pdf = build_pdf(parse_summary(markdown), source_name=filename)
    download_name = f"{Path(filename).stem}_summary.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "X-Cache": cache_status,
            "X-Summary-Id": summary_id,
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
