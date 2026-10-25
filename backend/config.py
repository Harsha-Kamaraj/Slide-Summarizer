"""Central settings. Every value can be overridden with an env var, so the same
code runs against local Ollama in development and a hosted API in production."""

import os

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
VISION_MODEL: str = os.getenv("VISION_MODEL", "gemma4:12b")  # revisited in Stage 5
LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "300"))
# "none" turns off Gemma 4's thinking mode, which roughly doubles latency.
# Set to "" to omit the field for providers that don't support it.
LLM_REASONING_EFFORT: str = os.getenv("LLM_REASONING_EFFORT", "none")
LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
# How many chunk-note calls run at once. Chunks are independent, so a hosted
# API can do them in parallel; a laptop GPU gains little, so default to 1.
LLM_PARALLEL_CALLS: int = int(os.getenv("LLM_PARALLEL_CALLS", "1"))

# --- Summarization ---
# Slides per map step. Small enough to fit the model's context window, large
# enough that related slides are summarized together.
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "8"))
# Below this many words, a slide is treated as "image-heavy" (Stage 5).
IMAGE_HEAVY_WORD_THRESHOLD: int = int(os.getenv("IMAGE_HEAVY_WORD_THRESHOLD", "15"))

# --- Upload limits ---
MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "50"))
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
