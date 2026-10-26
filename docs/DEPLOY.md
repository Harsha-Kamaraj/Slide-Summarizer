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

**Measured on Groq (October 2026):** use the large model for both text steps. Using the small model for chunk notes was 5× faster, but 10% of summary claims were unsupported (vs 2%), mostly wrong worked-example steps (DECISIONS #28).

| Variable | Groq value |
|---|---|
| `NOTES_MODEL`, `TEXT_MODEL` | `openai/gpt-oss-120b` |
| `VISION_MODEL` | `qwen/qwen3.8-27b` (the only Groq model that reads images; ~1000 output tokens/min on the free tier) |
| `LLM_REASONING_EFFORT` | `low` (gpt-oss rejects `none`) |
| `LLM_MAX_RETRIES` | `6` (rides out per-minute limits) |
| `FALLBACK_BASE_URL`, `FALLBACK_MODEL` | `https://api.groq.com/openai/v1`, `openai/gpt-oss-20b`: its own ~200k tokens/day, used when the main model's daily quota runs out (the deploy script sets this up) |
| `FALLBACK_VISION_MODEL` | `qwen/qwen3.8-27b` |

To protect against a full Groq outage instead, point the fallback at another provider, e.g. Google AI Studio: `FALLBACK_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`, `FALLBACK_MODEL=gemma-4-26b-a4b-it`. In testing (October 2026) it was slow, ~10 tokens/s plus visible "thinking" (which the backend strips), and often returned 500/503.

**Never put keys in the code or the extension.** They go in the host's secret settings only.

## 2. Deploy the backend

Three tested options (October 2026). Hugging Face Docker Spaces now need a paid PRO plan, and Google Cloud Run needs billing enabled, so neither is listed.

| Host | Card? | Always on? | Notes |
|---|---|---|---|
| **Render free** (recommended) | No | Sleeps after 15 min idle; ~50 s to wake | 0.1 CPU / 512 MB; fine thanks to the fast table reader (decision #31). Disk is wiped on sleep, so the cache lives in a private Hugging Face dataset |
| **Oracle Cloud Always Free** | Yes (verification only) | Yes, but idle servers may be reclaimed | 2 OCPU / 12 GB; `deploy/oracle/` sets up Caddy HTTPS + the backend |
| **Your laptop + a tunnel** | No | Only while the laptop is on | e.g. Tailscale Funnel for a stable public HTTPS address |

### Render (free, no card)

`render.yaml` describes the service, so Render can set it up as a Blueprint.

1. **Cache storage (one time):** on Hugging Face, create a private dataset (e.g. `<user>/slide-summarizer-cache`), then a **fine-grained** access token with write access to **only that dataset**. If the server is ever compromised, that token can't touch anything else.
2. **Sign up** at <https://render.com> with GitHub, and give Render access to this repository.
3. **New → Blueprint →** pick the repository. Render reads `render.yaml` and shows one free web service, `slide-summarizer`.
4. Render asks for the values marked `sync: false`, which are the secrets. Paste them into Render, never into the code:

   | Name | Value |
   |---|---|
   | `LLM_API_KEY` | your Groq key |
   | `FALLBACK_API_KEY` | the same Groq key (the fallback is a second Groq model) |
   | `HF_TOKEN` | the fine-grained Hugging Face token from step 1 |

5. **Apply.** The first build takes ~10 minutes (LibreOffice is large). Later pushes to `main` redeploy automatically.
6. Open `https://<service>.onrender.com/health`. It should return `{"status":"ok","ollama":true}`. (`ollama` is a historical name; it means "the AI provider is reachable".)

What the free tier means in practice:
- The first student after 15 quiet minutes waits ~50 s while the server wakes up. After that it's normal speed.
- Extraction is usually much faster than AI processing. Large decks can take many minutes under shared quotas; there is no fixed completion-time guarantee.
- Cached summaries survive sleeps and restarts (Hugging Face dataset). Feedback and daily-limit counts reset on restart.
- Tables ruled with plain lines (rare in PowerPoint exports) aren't read as tables there (`SLOW_TABLE_FALLBACK=0`).

**Backend 0.5.0 (compatible with extension 0.4.0):** estimates input size before AI calls, caps output, splits oversized notes/merges/extras without discarding source text, and reuses successful AI replies. A process-wide rolling-window limiter reserves estimated input plus maximum output per provider/model. Docker uses two parallel calls, a 7,200-token/minute budget and 25 requests/minute; these are conservative defaults for the currently configured account, not universal provider limits. Provider 429s remain authoritative because estimates and usage from other clients can differ. Retry-After is honoured up to 120 seconds; longer waits return a clear quota error or try a distinct fallback. A fallback using the same provider/model is not retried as another allowance.

Render Blueprint settings use 90-second request timeouts, two retries and a cooperative 20-minute processing budget (queue time excluded). Extraction, rendering and remote cache operations are not forcibly interrupted by the budget. Watch for dashboard environment overrides when applying `render.yaml` to an existing service; especially replace an old `LLM_MAX_RETRIES=6`. Changes in Docker defaults cannot override an explicitly configured Render environment variable.

Successful AI sections, extras, selected source images and submission/terminal job records are written locally and copied synchronously to the private HF dataset. Restart recovery depends on successful remote writes; failures are logged. Finished jobs can be restored until one hour after completion. Incomplete jobs ask for re-upload and reuse saved sections, rather than pretending a restarted worker is still running. This is a single-process queue, not a distributed durable worker system. API credentials and full uploads are not stored in checkpoints. Source images are retained: review `docs/PRIVACY.md` and use a private dataset.

`INCLUDE_DIAGRAMS=1` and `MAX_DIAGRAM_SLIDES=40` add a numbered original-slide appendix. Visual detection is heuristic and includes some charts/tables; a cap samples across the whole deck. No AI image generator is involved. If a diagram description fails, the PDF warns which slides need checking. Rebuild older summaries to get the new appendix; the pipeline version invalidates earlier result keys once.

For documentation/license-only commits, use `[skip render]` in the commit message to avoid restarting active jobs. For backend updates, finish active jobs before deployment. This release does not change the service's free plan or enable paid AI billing.

Validation commands: `.venv/bin/python -m pytest -q` and `node --test tests/test_extension_*.mjs` (Node 22+ for the test runner's ES module detection). Test the extension with DevTools closed, a Wi-Fi interruption, and a browser restart before sharing broadly.

### Oracle Cloud (always on, card for verification)

On a fresh Ubuntu 24.04 VM (Ampere A1, **2 OCPU / 12 GB** to stay within the free limit), with ports 80/443 opened in the VCN security list:

```bash
git clone https://github.com/Harsha-Kamaraj/Slide-Summarizer.git && cd Slide-Summarizer
bash deploy/oracle/setup.sh     # installs Docker, opens the firewall, asks for the Groq key, starts HTTPS
bash deploy/oracle/update.sh    # later: deploy the latest code
```

The address is `https://<ip-with-dashes>.sslip.io`, with a free certificate from Caddy. Free servers that stay idle for 7 days may be reclaimed by Oracle, so add an uptime monitor and restart the server from the console if it stops.

## 3. Point the extension at the server

- **For testing:** open the extension popup → **⚙** → enter the server address (e.g. `https://<service>.onrender.com`) → **Save**. Chrome asks for permission to contact that address.
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
