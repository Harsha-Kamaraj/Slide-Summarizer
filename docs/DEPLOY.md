# Deploying Slide Summarizer

Goal: classmates install only the Chrome extension. The backend runs on a free host, and a free hosted LLM API does the summarizing. Nothing in the code changes; it's all configuration.

```
[Extension] ──► [Backend in Docker on a free host] ──► [Hosted LLM API (OpenAI-compatible)]
                    caches summaries, queues jobs,        e.g. Groq; optional fallback
                    rate-limits, collects feedback         e.g. Google AI Studio
```

Free tiers and model lists change often, so check each provider's current limits and model names before relying on them.

## 1. Get an LLM API key

Any OpenAI-compatible provider works. Two free options:

| Provider | `LLM_BASE_URL` | Notes |
|---|---|---|
| Groq | `https://api.groq.com/openai/v1` | Very fast; free tier is rate-limited per model (see its console) |
| Google AI Studio | `https://generativelanguage.googleapis.com/v1beta/openai/` | Offers Gemma (same family we tuned on); free-tier prompts may be used by Google to improve its products |

Create a key in the provider's console and pick model names from its model list: a text model, and a vision-capable model for diagram slides. If there's no vision model, set `VISION_ENABLED=0`.

**Never put keys in the code or the extension.** They go in the host's secret settings only.

## 2. Deploy the backend (Hugging Face Spaces, free)

Hugging Face Spaces runs Docker containers on free CPU hardware. The container runs as user 1000 on port 7860, which the `Dockerfile` already handles.

1. Create a Space at <https://huggingface.co/new-space> and choose **Docker** as the SDK.
2. The Space's `README.md` needs this block at the top (Hugging Face reads it to know how to run the app):
   ```yaml
   ---
   title: Slide Summarizer
   sdk: docker
   app_port: 7860
   ---
   ```
3. Put `Dockerfile` and the `backend/` folder from this repo into the Space repository and push. Hugging Face builds the image automatically.
4. In the Space's **Settings → Variables and secrets**, add:

   | Name | Type | Example |
   |---|---|---|
   | `LLM_API_KEY` | **Secret** | your provider key |
   | `LLM_BASE_URL` | Variable | `https://api.groq.com/openai/v1` |
   | `TEXT_MODEL` | Variable | a text model from the provider |
   | `VISION_MODEL` | Variable | a vision model, or set `VISION_ENABLED=0` |
   | `FALLBACK_BASE_URL`, `FALLBACK_MODEL` | Variable | optional second provider |
   | `FALLBACK_API_KEY` | **Secret** | optional |
   | `DAILY_LIMIT` | Variable | new summaries per student per day (image default: 10) |

5. Open `https://<user>-<space>.hf.space/health`. It should return `{"status":"ok","ollama":true}`. (The field is named `ollama` for historical reasons; it means "the LLM provider is reachable".)

**Know the trade-offs of the free tier:**
- **Disk is wiped on restart,** so the summary cache and feedback start empty again. Attach a storage bucket and set `CACHE_DIR=/data/cache` and `FEEDBACK_FILE=/data/feedback.jsonl` to keep them.
- **Free Spaces sleep when unused.** The first request after a sleep waits while the container starts.
- **One container:** jobs, rate-limit counts and the in-flight job list live in memory, which is fine for one container.

Any Docker host works the same way: Render, Google Cloud Run or a VM. Run the same image with the same environment variables, and set `PORT` if the host requires a specific port.

## 3. Point the extension at the server

- **For testing:** open the extension popup → **Settings** → enter `https://<user>-<space>.hf.space` → **Save**. Chrome asks for permission to contact that address.
- **For everyone:** set `DEFAULT_BACKEND_URL` in `extension/config.js` to the hosted URL, bump `version` in `manifest.json`, and reload or republish.

## 4. Share it

- **Until it's on the Web Store:** zip the `extension/` folder. Classmates unzip it, open `chrome://extensions`, turn on Developer mode, and click **Load unpacked**.
- **Chrome Web Store:** you need a developer account (one-time fee), the 128 px icon, at least one screenshot, a description, and a privacy policy URL. Host `docs/PRIVACY.md`, for example through GitHub Pages or a link to the file on GitHub. In the "single purpose" and permission justifications, say that host access to pesuacademy.com is used to add buttons and download the slides the student picks.
- **Get the college's okay before publishing.** The extension works with PESU Academy content.

## 5. Check it's working

- `GET /feedback/stats` → `{"up": …, "down": …, "positive_%": …}` for the resume line "Y% positive".
- `docker run` locally first if you like (uses your Ollama):
  ```bash
  docker build -t slide-summarizer .
  docker run -p 7860:7860 -e LLM_BASE_URL=http://host.docker.internal:11434/v1 \
             -e LLM_REASONING_EFFORT=none slide-summarizer
  curl localhost:7860/health
  ```
