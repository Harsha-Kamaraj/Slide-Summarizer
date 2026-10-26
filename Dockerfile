# Backend image for hosting (e.g. Hugging Face Spaces, Render, Cloud Run).
FROM python:3.13-slim

# LibreOffice converts .ppt and renders PowerPoint slides for the vision
# model; DejaVu is the PDF font (covers Greek and math symbols).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-impress fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Run as a normal user (Hugging Face Spaces requires uid 1000). LibreOffice
# also needs a writable home directory for its profile.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

# Dependencies first, so code changes don't reinstall them on every build.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend

USER app

# Production defaults; the LLM provider and keys come from the host's secret
# settings (LLM_BASE_URL, LLM_API_KEY, TEXT_MODEL, ...), never the image.
# Cache and feedback live in the home folder (created at runtime). Many hosts
# wipe the disk on restart: point CACHE_DIR/FEEDBACK_FILE at a persistent
# volume (e.g. /data on Hugging Face with storage attached) to keep them.
# One job at a time: two PDFs at once can exceed a 512 MB free instance.
ENV HOME=/home/app \
    PORT=7860 \
    CACHE_DIR=/home/app/data/cache \
    FEEDBACK_FILE=/home/app/data/feedback.jsonl \
    DAILY_LIMIT=10 \
    TRUST_PROXY_HEADERS=1 \
    JOB_WORKERS=1 \
    LLM_PARALLEL_CALLS=2 \
    LLM_TIMEOUT_SECONDS=90 \
    LLM_TOKENS_PER_MINUTE=7200 \
    LLM_REQUESTS_PER_MINUTE=25 \
    VISION_MAX_TOKENS=450 \
    LLM_REASONING_EFFORT=

EXPOSE 7860
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
