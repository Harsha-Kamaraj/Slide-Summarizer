"""Deploy the backend to a Hugging Face Docker Space.

Usage (from the repo root):
    .venv/bin/python scripts/deploy_hf_space.py <username>/slide-summarizer

Needs these env vars, read here and never printed or written to disk:
    HF_TOKEN        a Hugging Face access token with write permission
    GROQ_API_KEY    the main LLM provider key, stored in the Space as a *secret*

What it does: creates the Space if needed (Docker SDK, public, so the
extension can call it), sets the model settings as Space variables and the API
key as a secret, then uploads Dockerfile + backend/. Hugging Face builds and
starts the container. Re-run it to deploy updates.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hosted settings chosen by evaluation (docs/DECISIONS.md #27-28).
VARIABLES = {
    "LLM_BASE_URL": "https://api.groq.com/openai/v1",
    "NOTES_MODEL": "openai/gpt-oss-120b",
    "TEXT_MODEL": "openai/gpt-oss-120b",
    "VISION_MODEL": "qwen/qwen3.8-27b",
    "LLM_REASONING_EFFORT": "low",
    "LLM_MAX_RETRIES": "6",
    "DAILY_LIMIT": "10",
}

# Fallback when the main model is rate-limited or out of daily quota (the
# common failure): gpt-oss-20b on Groq has its own per-model quota and was
# tested as a working fallback (decision #30). Google AI Studio's Gemma was
# tried first but was unreliable (500/503, timeouts); see docs/DEPLOY.md.
FALLBACK_VARIABLES = {
    "FALLBACK_BASE_URL": "https://api.groq.com/openai/v1",
    "FALLBACK_MODEL": "openai/gpt-oss-20b",
    "FALLBACK_VISION_MODEL": "qwen/qwen3.8-27b",
    "FALLBACK_REASONING_EFFORT": "low",
}

# Hugging Face reads this block to know the Space is a Docker app on port 7860.
SPACE_README = """---
title: Slide Summarizer
emoji: 📝
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

Backend for the [Slide Summarizer](https://github.com/Harsha-Kamaraj/Slide-Summarizer)
Chrome extension. API only: `GET /health`, `POST /jobs`, `GET /jobs/{id}`.
"""


def main() -> None:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        sys.exit("Usage: deploy_hf_space.py <username>/<space-name>")
    repo_id = sys.argv[1]
    token, llm_key = os.environ.get("HF_TOKEN"), os.environ.get("GROQ_API_KEY")
    if not token or not llm_key:
        sys.exit("Set HF_TOKEN and GROQ_API_KEY in your environment first.")

    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    print(f"Space ready: https://huggingface.co/spaces/{repo_id}")

    api.add_space_secret(repo_id, "LLM_API_KEY", llm_key)
    api.add_space_secret(repo_id, "FALLBACK_API_KEY", llm_key)  # same provider
    variables = VARIABLES | FALLBACK_VARIABLES
    for name, value in variables.items():
        api.add_space_variable(repo_id, name, value)
    print(f"Set the API key secret(s) and {len(variables)} variables")

    # Upload only what the image needs, from a clean copy (no caches).
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp)
        shutil.copy(REPO_ROOT / "Dockerfile", bundle / "Dockerfile")
        shutil.copytree(
            REPO_ROOT / "backend", bundle / "backend", ignore=shutil.ignore_patterns("__pycache__")
        )
        (bundle / "README.md").write_text(SPACE_README)
        api.upload_folder(
            repo_id=repo_id,
            repo_type="space",
            folder_path=bundle,
            commit_message="Deploy backend",
            delete_patterns=["backend/**"],  # remove files deleted locally
        )

    user, name = repo_id.split("/")
    app_url = f"https://{user}-{name}.hf.space".lower().replace("_", "-")
    print("Uploaded. Hugging Face is now building the image (a few minutes).")
    print(f"When it's running, check: {app_url}/health")


if __name__ == "__main__":
    main()
