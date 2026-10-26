"""Background jobs, so a summary doesn't need one HTTP request held open for
minutes.

POST /jobs saves the upload and returns a job id straight away; a small pool
of worker threads processes jobs in order (a queue); the extension polls
GET /jobs/{id} for status and progress text ("Summarizing part 3/8…") and
then downloads GET /jobs/{id}/pdf.

One process runs the queue. Submission and terminal records are saved in the
cache. After restart a completed job can be downloaded until its TTL; an
unfinished job requires re-upload, reusing cached AI sections. Source files
are never persisted remotely, so this is not an automatically resumed queue.
"""

import logging
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from backend import cache, config, execution, llm, service
from backend.extract import ExtractionError

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    filename: str
    status: str = "queued"  # queued | running | done | error
    progress: str = "Waiting in queue…"
    error: str = ""
    markdown: str = ""
    from_cache: bool = False
    summary_id: str = ""  # identifies the summary for feedback
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    finished: float = 0


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=max(1, config.JOB_WORKERS))


def submit(upload_dir: Path, path: Path, content_hash: str, mode: str, extras: list[str]) -> Job:
    """Queue a saved upload for processing. Takes ownership of upload_dir and
    deletes it once the job finishes (privacy: we never keep the files)."""
    _forget_old_jobs()
    job = Job(id=uuid.uuid4().hex, filename=path.name)
    job.summary_id = service.summary_id(content_hash, mode)
    with _jobs_lock:
        _jobs[job.id] = job
    _executor.submit(_run, job, upload_dir, path, content_hash, mode, extras)
    return job


def get(job_id: str) -> Job | None:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return None
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        stored = cache.get(_record_key(job_id))
        if stored:
            try:
                job = Job(**json.loads(stored))
            except (ValueError, TypeError):
                return None
            if job.status in ("queued", "running"):
                # Uploaded sources aren't retained. A new upload can reuse all
                # persisted AI replies; don't pretend an orphan is still running.
                _fail(
                    job,
                    "The server restarted. Retry with the same file and settings to reuse saved sections.",
                )
                _save(job)
            with _jobs_lock:
                job = _jobs.setdefault(job_id, job)
    if job and job.finished and time.time() - job.finished > config.JOB_TTL_SECONDS:
        return None
    if job and job.status == "queued":
        job.progress = _queue_message(job)
    return job


def _run(job: Job, upload_dir: Path, path: Path, content_hash: str, mode: str, extras: list[str]):
    job.status = "running"
    # Remote persistence must not hold POST /jobs open until the browser's
    # upload timeout. Save in the worker before starting expensive AI work.
    _save(job)
    result = replace(job)

    def report(message: str) -> None:
        job.progress = message
        job.updated = time.time()

    try:
        result.markdown, result.from_cache = service.run(path, content_hash, mode, extras, report)
        result.status = "done"
        result.progress = "Done"
        result.finished = time.time()
    except ExtractionError as exc:
        _fail(result, str(exc))
    except llm.LLMError as exc:
        logger.error("LLM failure for %s: %s", job.filename, exc)
        _fail(result, f"The summarizer is unavailable right now: {exc}")
    except execution.DeadlineExceeded as exc:
        _fail(result, str(exc))
    except Exception:
        # A worker thread must never die silently: log it, tell the student.
        logger.exception("Job %s failed unexpectedly", job.id)
        _fail(result, "Something went wrong while summarizing. Please try again.")
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)
        report("Saving the completed result…" if result.status == "done" else "Saving job status…")
        result.updated = time.time()
        _save(result)
        # Publish terminal status only after saving and deleting the upload.
        with _jobs_lock:
            _jobs[job.id] = result


def _fail(job: Job, message: str) -> None:
    job.status = "error"
    job.error = message
    job.progress = "Failed"
    job.finished = time.time()


def _record_key(job_id):
    return cache.object_key("job-record-v1", job_id)


def _save(job):
    try:
        cache.put(_record_key(job.id), json.dumps(asdict(job)))
    except OSError:
        logger.exception("Could not save job record")


def _queue_message(job: Job) -> str:
    with _jobs_lock:
        ahead = sum(
            1
            for other in _jobs.values()
            if other.created < job.created and other.status in ("queued", "running")
        )
    # Jobs that are running don't make you wait if a worker is free.
    waiting = max(0, ahead - config.JOB_WORKERS + 1)
    return f"Waiting in queue ({waiting} ahead)…" if waiting else "Starting…"


def _forget_old_jobs() -> None:
    cutoff = time.time() - config.JOB_TTL_SECONDS
    with _jobs_lock:
        # Only finished jobs: a slow job must never vanish while it runs.
        finished = [j.id for j in _jobs.values() if j.status in ("done", "error")]
        for job_id in [i for i in finished if _jobs[i].finished < cutoff]:
            del _jobs[job_id]


def new_upload_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="slide-summarizer-"))
