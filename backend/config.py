"""Central settings. Every value can be overridden with an env var, so the same
code runs against local Ollama in development and a hosted API in production."""

import os
from pathlib import Path

# --- LLM provider (OpenAI-compatible Chat Completions API) ---
# Ollama serves the OpenAI-compatible API under /v1. For a hosted provider,
# change these three env vars; no code changes needed.
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
# Ollama ignores the key, but the OpenAI format expects one to be sent.
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "ollama")
TEXT_MODEL: str = os.getenv("TEXT_MODEL", "gemma4:12b")
# Model for the map step (chunk notes). Defaults to TEXT_MODEL; can be set to a
# smaller/faster model, since extracting notes is easier than the final merge.
NOTES_MODEL: str = os.getenv("NOTES_MODEL", TEXT_MODEL)
# Model that checks summaries against the slides in eval/evaluate.py. Ideally
# a different (stronger) model than the summarizer, to avoid grading its own work.
JUDGE_MODEL: str = os.getenv("JUDGE_MODEL", TEXT_MODEL)
VISION_MODEL: str = os.getenv("VISION_MODEL", "gemma4:12b")  # must support image input
# Describe image-heavy slides with the vision model (Stage 5). Set to 0 to skip
# it, e.g. with a hosted text-only model or to save time.
VISION_ENABLED: bool = os.getenv("VISION_ENABLED", "1") == "1"
# Upper bound on vision calls per deck, so an all-image deck can't take forever.
MAX_VISION_SLIDES: int = int(os.getenv("MAX_VISION_SLIDES", "20"))
# Cap on each slide description's length. Descriptions are short anyway, and
# some hosted free tiers (e.g. Groq's vision model) only allow ~1000 output
# tokens per minute, rejecting any request that could exceed it.
VISION_MAX_TOKENS: int = int(os.getenv("VISION_MAX_TOKENS", "900"))
# Local per-request default; the hosted Docker/Blueprint configuration uses
# 90 seconds to avoid repeated 15-minute waits on stalled hosted requests.
LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "900"))
# "none" turns off Gemma 4's thinking mode, which roughly doubles latency.
# Set to "" to omit the field for providers that don't support it.
LLM_REASONING_EFFORT: str = os.getenv("LLM_REASONING_EFFORT", "none")
LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
# How many chunk-note calls run at once. Chunks are independent, so a hosted
# API can do them in parallel; a laptop GPU gains little, so default to 1.
LLM_PARALLEL_CALLS: int = int(os.getenv("LLM_PARALLEL_CALLS", "1"))
LLM_TOKENS_PER_MINUTE: int = int(os.getenv("LLM_TOKENS_PER_MINUTE", "0"))
LLM_REQUESTS_PER_MINUTE: int = int(os.getenv("LLM_REQUESTS_PER_MINUTE", "0"))
LLM_IMAGE_TOKEN_ESTIMATE: int = int(os.getenv("LLM_IMAGE_TOKEN_ESTIMATE", "2500"))
LLM_INPUT_TOKEN_BUDGET: int = int(os.getenv("LLM_INPUT_TOKEN_BUDGET", "4500"))
LLM_OUTPUT_TOKEN_BUDGET: int = int(os.getenv("LLM_OUTPUT_TOKEN_BUDGET", "2200"))
NOTES_MAX_TOKENS: int = int(os.getenv("NOTES_MAX_TOKENS", "1200"))
JOB_MAX_SECONDS: int = int(os.getenv("JOB_MAX_SECONDS", "1200"))
INCLUDE_DIAGRAMS: bool = os.getenv("INCLUDE_DIAGRAMS", "1") == "1"
MAX_DIAGRAM_SLIDES: int = int(os.getenv("MAX_DIAGRAM_SLIDES", "40"))

# Optional fallback provider, used when the main one is rate-limited, down or
# timing out (e.g. a second free tier). Leave FALLBACK_BASE_URL empty to disable.
FALLBACK_BASE_URL: str = os.getenv("FALLBACK_BASE_URL", "")
FALLBACK_API_KEY: str = os.getenv("FALLBACK_API_KEY", "")
FALLBACK_MODEL: str = os.getenv("FALLBACK_MODEL", "")
# Leave empty if the fallback can't read images: while the main provider is
# unavailable, image slides are then skipped instead of described.
FALLBACK_VISION_MODEL: str = os.getenv("FALLBACK_VISION_MODEL", "")
FALLBACK_REASONING_EFFORT: str = os.getenv("FALLBACK_REASONING_EFFORT", "")

# --- Summarization ---
# Slides per map step. Small enough to fit the model's context window, large
# enough that related slides are summarized together.
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "8"))
# Below this many words, a slide is treated as "image-heavy" (Stage 5).
IMAGE_HEAVY_WORD_THRESHOLD: int = int(os.getenv("IMAGE_HEAVY_WORD_THRESHOLD", "15"))

# --- Upload limits ---
MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "50"))
# Tables drawn as cell rectangles (PowerPoint exports, like the college's
# PDFs) are read by a fast reader. Tables ruled with plain lines need PyMuPDF's
# find_tables(), which costs ~26 s/page on a 0.1-CPU free host: turn it off
# there with SLOW_TABLE_FALLBACK=0.
SLOW_TABLE_FALLBACK: bool = os.getenv("SLOW_TABLE_FALLBACK", "1") == "1"
MAX_SLIDES: int = int(os.getenv("MAX_SLIDES", "150"))
ALLOWED_EXTENSIONS: tuple[str, ...] = (".pptx", ".ppt", ".pdf")

# --- PDF output ---
# Folders searched for DejaVu Sans, a free font that covers Greek letters and
# math symbols (reportlab's built-in Helvetica can't draw e.g. "χ").
# LibreOffice bundles it on macOS; on Debian/Docker it's the fonts-dejavu package.
FONT_DIRS: list[str] = [
    d
    for d in [
        os.getenv("FONT_DIR", ""),
        "/Applications/LibreOffice.app/Contents/Resources/fonts/truetype",
        "/usr/share/fonts/truetype/dejavu",
    ]
    if d
]

# --- Cache ---
# Finished summaries (Markdown) keyed by file hash + mode + prompt/model version.
CACHE_DIR: str = os.getenv("CACHE_DIR", str(Path(__file__).parent.parent / ".cache" / "summaries"))
# Optional persistent copy of the cache in a private Hugging Face dataset
# ("user/name"), for hosts whose disk is wiped on restart. HF_TOKEN needs write
# access to that dataset only (use a fine-grained token).
CACHE_HF_REPO: str = os.getenv("CACHE_HF_REPO", "")
HF_TOKEN: str = os.getenv("HF_TOKEN", "")

# --- Serving many students (Stage 8) ---
# Jobs processed at once; the rest wait in a queue. 1 suits a laptop running
# Ollama; a hosted API can take more.
JOB_WORKERS: int = int(os.getenv("JOB_WORKERS", "1"))
# Finished jobs are forgotten after this long (their summaries stay cached).
JOB_TTL_SECONDS: int = int(os.getenv("JOB_TTL_SECONDS", "3600"))
# New summaries per student (per IP address) per day; cached summaries don't
# count. 0 = unlimited (local development).
DAILY_LIMIT: int = int(os.getenv("DAILY_LIMIT", "0"))
# Behind a hosting proxy, the student's IP is in X-Forwarded-For rather than
# the connection address. Only enable when such a proxy is in front.
TRUST_PROXY_HEADERS: bool = os.getenv("TRUST_PROXY_HEADERS", "0") == "1"
# Thumbs up/down feedback, one JSON object per line.
FEEDBACK_FILE: str = os.getenv(
    "FEEDBACK_FILE", str(Path(__file__).parent.parent / ".data" / "feedback.jsonl")
)
