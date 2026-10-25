# Design Decisions

A running log of non-obvious choices and why they were made. Newest at the bottom.

## 1. A Python backend sits between the extension and the LLM
Parsing PPTX files, rendering slides to images and building PDFs are all mature, easy tasks in Python and awkward in browser JavaScript. Putting them in a backend keeps the extension thin (it just fetches a file and downloads a PDF). It also sidesteps Ollama's CORS rules, which block requests from `chrome-extension://` origins by default, and it means that in production the LLM API key lives on the server, never inside the extension where anyone could read it.

## 2. The extension fetches the PPT, not the backend
Lecture files on the college site sit behind a student login. The extension runs inside the student's browser, so its requests already carry their session cookies. The backend has no session and would need the student's password to log in, which we never want to handle. So the extension downloads the bytes and uploads them to the backend.

## 3. One LLM interface using the OpenAI-compatible Chat Completions format
All model calls go through `backend/llm.py`, which speaks the OpenAI Chat Completions format. Ollama serves this format at `/v1`, and so do most hosted providers. Moving from local development to a hosted API is then a configuration change (`LLM_BASE_URL`, `LLM_API_KEY`, model name), not a code change. The rest of the code never knows which provider it is talking to.

## 4. Default models for a 16 GB Apple Silicon Mac
The development machine is an M4 with 16 GB of unified memory, which is shared between the OS, the browser and the model. The default text model is `gemma4:12b` (about 7.7 GB). It is the largest model that fits comfortably and gives noticeably better summaries than 4B models. `gemma3:4b` (3.3 GB) is the fast fallback, and you can switch to it by setting `TEXT_MODEL`. Both are already pulled locally.

`gemma4:12b` also reports `vision` capability (`ollama show`), so it is the default vision model too. A single model means only one model's weights are in memory, with no reloads or swapping between a text model and a vision model, which matters on 16 GB. This will be re-checked against real image-heavy slides in Stage 5.

Note: `gemma4:12b` has "thinking" on by default. A trivial one-word reply took about 11 seconds because of it. For summarization we will turn thinking off unless the eval in Stage 6 shows it improves faithfulness enough to be worth the time.

## 5. Python 3.13 for the virtual environment
The machine has both 3.13 and 3.14. We use 3.13 because every dependency (PyMuPDF, reportlab, python-pptx) is known to ship prebuilt wheels for it, and it still meets the 3.11+ requirement. It also matches the slim Docker base images we'll use when deploying.

## 6. `backend` is a Python package, run from the repo root
The Stage 1 CLI is `python -m backend.summarize`, which requires `backend/` to be a package imported from the repo root. To keep one consistent import style (`from backend.config import ...`), the API server will also be started from the root with `uvicorn backend.main:app`, not with `cd backend && uvicorn main:app`. Mixing the two styles breaks imports in one mode or the other.

## 7. PDF is a first-class input, not an afterthought
The college site (PESU Academy, `pesuacademy.com/Academy/s/studentProfilePESU#`) lists slides under Course → Unit → **Slides** tab. Each entry (e.g. "ML models") has an eye icon that opens a viewer, and from there the student can save the deck as a PDF. Lecturers upload .pptx, .ppt and PDF, but what the browser actually receives is often a PDF. So the extractor treats all three as equal inputs: .pptx through python-pptx (which also gives us speaker notes), PDF through PyMuPDF (one page = one slide), and legacy .ppt by converting to .pptx with LibreOffice headless first.

The site is a single-page app (the URL ends in `#` and content changes without page loads), so the Stage 4 content script will need a `MutationObserver` to notice when the Slides tab renders, not just a one-time scan on page load. The exact request the eye icon makes is still to be captured from DevTools before Stage 4.

## 8. Map-reduce summarization
A 60-slide deck is too much text to summarize well in one prompt on a 12B model. The model would lose detail in the middle, and slower local models get much slower as the prompt grows. So we **map**: split the slides into chunks of `CHUNK_SIZE` (8) and turn each chunk into revision notes. Then we **reduce**: send all the chunk notes to one final prompt that merges duplicate topics and produces the finished document. Chunks of about 8 slides are big enough that a topic spanning several slides stays together, and small enough to fit any model's context window with plenty of room. The trade-off is one extra LLM call per chunk, and a topic split across a chunk boundary is reassembled in the reduce step.

## 9. Prompts are plain text files with a shared system prompt
Prompts live in `backend/prompts/*.txt`, not in Python strings, so they can be read and tweaked without touching code, and prompt changes show up clearly in git diffs. The anti-hallucination rules (use only slide content, say "not covered in slides") sit in one `system.txt` that every call shares, so they can't drift apart between steps. Templates use Python's `string.Template` (`$name`) rather than `str.format` (`{name}`), because lecture content and prompts often contain literal braces (code, set notation) that would break `format`.

## 10. The summary is Markdown with fixed headings
The final output uses fixed Markdown headings (`## Overview`, `## Key Points`, `## Important Terms`). Markdown is readable as-is in the terminal (Stage 1), small models produce it reliably, and the PDF builder (Stage 2) can turn it into sections by splitting on those known headings. JSON output was the alternative, but it is unreadable in the terminal, and small models sometimes break the syntax. Constrained decoding would fix that, but it ties us to providers that support it.

## 11. Strip text that repeats on half the PDF pages
The college's PDF exports put the course name ("Machine Learning") on every page. In some decks it appears *above* the real heading, so it was taken as every slide's title. Any line that appears on at least half the pages (for decks of 4+ pages) is treated as a running header or footer and removed before the slide title is chosen. This works for any course without hardcoding course names. Bullet glyphs (`•`, `●`, `■`) that PowerPoint exports as text are also turned into `- `, except `-` itself, which could be a minus sign.

## 12. The model names the summary; the extractor doesn't try
Slide 1 of these decks is a course/credits slide and the PDF metadata title is "Slide 1", so the extractor can't find the lecture's topic. Instead of guessing, the final prompt asks the model to write a short topic title from the notes, with the deck's label given only as context. When the extension is built, the deck name shown on the site (e.g. "ML models") can be passed in too.

## 13. Check the summary's structure in code, and give the model one retry
On a real 35-slide deck the model sometimes repeated the template mid-document (a second `## Overview`), which would break the PDF builder. Clearer prompt wording reduces this but can't guarantee it, so `structure_problem()` checks that there's one `# ` title and exactly the three `## ` sections in order. If the check fails, the model gets one follow-up message naming the exact problem and asking it to fix the output. Rules in the prompt make good output likely; a check in code catches the times it goes wrong anyway. Only one retry, to keep the time cost predictable.

## 14. PDF layout with reportlab, font from LibreOffice/DejaVu
reportlab builds the PDF in Python with no external tools, and our layout is simple (headings, bullets, a two-column terms table). WeasyPrint (HTML→PDF) would allow CSS styling but needs system libraries (Pango, Cairo) that complicate installs and Docker images. reportlab's built-in Helvetica can't draw characters like `χ`, which appeared in a real ML deck, so we use **DejaVu Sans**. It's free, covers Greek and math symbols, ships inside LibreOffice on macOS, and is the standard `fonts-dejavu` package on Linux, so no font files are committed. The builder parses the Markdown into a `Summary` object first and renders it second, which keeps each step testable. It's also lenient: it accepts `**Term**:`, `**Term:**` and `Term:`, because small models aren't consistent about formatting. Model text is HTML-escaped before rendering, because reportlab paragraphs parse mini-HTML and a formula like `<P,T,E>` would otherwise be read as a tag and vanish.

## 15. Measured: a 4B model is not good enough for the merge step
On the real 35-slide concept-learning deck, `gemma3:4b` finished in 194 s against about 240 s for `gemma4:12b`. But its summary covered only the first ~10 slides and dropped Version Space and Find-S (half the lecture), left one topic empty, and gave a definition the slides didn't support. The 12B model covered every topic. So `gemma4:12b` stays the default. A 4B model for the map step only (with 12B for the merge) is still worth evaluating in Stage 6, since copying facts out of 8 slides is an easier job than merging a whole lecture.

## 16. "Detailed" is the default mode, and the mode reaches both steps
Feedback after reading the first real summaries: they were too short to revise from. The original default ("short") capped output at 4 topics × 4 bullets. Detailed mode now gives each topic a plain-language explanation paragraph, then complete bullets, numbered algorithm steps, and worked examples written out with steps and answers. It's the default, and `short` stays available for quick revision. The important fix was passing the mode to the **map** step too: before, the chunk notes were always terse, and the merge can't put back detail the notes dropped without inventing it. The explanation paragraphs are still limited to slide content (the system prompt's rule), so "detailed" means more of what the lecturer said, not extra outside knowledge. The trade-off is longer generation time, because output length is what dominates time on a local model.

## 17. Explained bullets, and formulas on their own `Formula:` line in a box
Feedback on detailed mode: keep bullet points, but each bullet should *explain* (1–3 full sentences: what it means, why it matters, the lecture's example), not just name the idea. Formulas should stand out. The model writes every formula on its own line starting with `Formula:`. This is a plain-text marker small models follow reliably, unlike LaTeX, which we banned (decision 9), or a custom syntax. The PDF builder draws those lines as shaded, bordered boxes and turns plain-text math into real superscripts and subscripts (`2^(2^d)`, `x_i`). It handles the innermost brackets first, so nested powers display correctly. Superscript conversion runs only inside formula boxes. In normal sentences an underscore or caret might not be math, so it's left alone there.

## 18. Benchmark: keep 12B for everything locally; real speed needs a hosted API
`eval/benchmark.py` compared four local setups on the 35-slide deck in detailed mode (results in `eval/benchmark_results.md`):
- **12B everywhere:** 527 s, 2,260 words, 14 topics.
- **12B with 2 parallel chunk calls:** 496 s, only 6% faster. Output stayed at about 12 tokens/s, which shows the Mac is limited by memory speed: parallel requests share the same GPU and the same weight reads. Parallel calls stay in the code (`LLM_PARALLEL_CALLS`) because hosted APIs serve requests independently, and there the map step would take about as long as one call.
- **gemma3:4b notes + 12B merge:** 348 s, 34% faster but about half the words. It skipped the intermediate steps of the worked example, which is exactly the detail students asked for.
- **llama3.1:8b notes + 12B merge:** 398 s, the worst quality (7 topics, 1 formula, practice problems dropped).

So 12B stays the default, and `NOTES_MODEL=gemma3:4b` is a quick-iteration option. A deck uses about 16.5k tokens (10k in, 6k out), which sets how many new decks a free hosted tier can handle per day. The benchmark also found a faithfulness error in the 12B output: a worked-example step named the wrong attribute and gave a reason the slides don't state. That makes the Stage 6 "is every bullet supported by the slides?" check essential, not optional.

## 19. API design: sync endpoint, temp folder, meaningful status codes
- **`def`, not `async def`, for `/summarize`.** Summarizing blocks for minutes (HTTP calls to the model and CPU work in parsing and PDF building). If an `async` endpoint did that, it would block FastAPI's event loop and freeze every other request. FastAPI runs a plain `def` endpoint in a worker thread, so other requests stay responsive. Verified: `/health` answered in 0.04 s while a summary was running.
- **Uploads go to a temp folder under their original name** and are deleted when the request ends. That's the privacy promise (we keep no student files), and it makes error messages and fallback titles say "Lecture3.pptx" instead of "tmpab12". The filename is reduced to its last path component, so a crafted name like `../../x.pdf` can't escape the folder.
- **Size limit while streaming:** the upload is read in 1 MB pieces and rejected as soon as it passes `MAX_UPLOAD_MB`, rather than after loading it all into memory.
- **Status codes say what went wrong:** 415 for the wrong type, 413 for too large, 422 for an unreadable or invalid file, 503 when the LLM is unavailable. Each comes with a readable `detail` message the extension can show students directly, never a stack trace.
- **`/health` checks the LLM via `GET /models`,** which is part of the OpenAI-compatible API, so it works unchanged with Ollama or a hosted provider and costs no generation.
- **Known limit:** one HTTP request is held open for the whole summary (minutes). That's fine locally, but proxies and browsers may time out. Stage 8 replaces it with `POST /jobs` + `GET /jobs/{id}` polling and progress updates.
- `extras` (exam questions, flashcards) from the original API spec is deferred to Stage 7 rather than accepted and silently ignored.

## 20. Extract PDF tables as rows, reading cells by position
A worked example kept naming the wrong attribute ("forecast" instead of "water"). The cause was the extractor, not just the model: plain PDF text extraction reads a table one cell per line (`sky`, `temp`, … `sunny`, `warm`, …), so the model had to count positions to know which column a value was in, and it miscounted. Now `page.find_tables()` finds ruled tables (14 of the 35 slides in the concept-learning deck have one), and each is written as `sky | temp | humidity | …` rows, matching how .pptx tables are already handled. Text inside a table's area is left out of the normal flow, so nothing appears twice. The cell text is read with `get_textbox(cell_rect)` instead of the table's own `extract()`, because `extract()` scrambled letter-spaced text in these exports ("s y" for "sky"). Columns that are empty in every row are dropped, because double cell borders can be detected as phantom columns. Lesson: when the model gets something wrong, check what it was actually given first. Verified on a re-run: the worked example now says "the water attribute changed from warm to cool", and the invented "more general" reasoning disappeared once the model could see the table structure.

## 21. How the extension gets the slides from PESU
Found with DevTools: each deck on the Slides tab is `<a onclick="loadIframe('/Academy/a/referenceMeterials/downloadslidecoursedoc/<id>…')">`. A `GET` on that path (with the student's session) answers **302** with a redirect to a CloudFront/S3 **signed URL** (`Expires=…&Signature=…`, valid about an hour), which serves `application/pdf`. Consequences:
- We **can't build the file URL ourselves** (only PESU's server can sign it), but we don't need to: the extension fetches the PESU path, and `fetch` follows the redirect to the PDF. Every click gets a freshly signed link, so nothing goes stale.
- The id comes from the link's `onclick`, and the deck title from the link's text, so the content script finds every deck with one selector: `a[onclick*="downloadslidecoursedoc/"]`.
- PESU always serves PDF here, even for decks uploaded as PowerPoint, which is why decision 7 made PDF a first-class input.
- We never store or log signed links; they work for anyone until they expire.

## 22. Extension architecture: thin content script, work in the background worker
- **`content.js`** only adds buttons and sends `{type: "summarize-url", url, title}` to the background worker. The button goes *after* PESU's link, not inside it, so clicking it doesn't also open PESU's viewer. A `MutationObserver` (batched per animation frame) adds buttons whenever the single-page app renders a Slides tab.
- **`background.js`** does the network work. It has our host permissions (so cross-origin requests to `files.pesuacademy.com` and `localhost` aren't blocked by CORS), and it keeps running after the popup closes. It checks that PESU returned a PDF and not a login page, which happens when the session expires, and shows a clear message in that case.
- **Long requests vs. service worker lifetime:** Chrome stops an idle extension service worker after about 30 s, even mid-fetch, and local summaries take minutes. A cheap extension API call every 20 s resets the idle timer. Stage 8's job polling (`POST /jobs`, `GET /jobs/{id}`) is the real fix, and it adds progress updates.
- **Downloads:** service workers have no `URL.createObjectURL`, so the PDF goes to `chrome.downloads.download` as a base64 `data:` URL (built in chunks to avoid call-stack overflow on large arrays).
- **Popup file picker:** extension messages can't carry binary data, so the popup base64-encodes the file and the background worker does the upload. That's why closing the popup doesn't cancel it.
- Settings (summary length, backend URL) live in `chrome.storage.sync`. The backend URL defaults to `localhost:8000` and will default to the hosted URL in Stage 8.

## 23. Cache summaries by file content, mode and pipeline version
Many students summarize the same lecture, so the backend caches finished summaries (`backend/cache.py`):
- **The key is the SHA-256 of the file's bytes**, computed while streaming the upload so the file is read only once. URLs can't be the key: PESU signs a new URL on every click. The key also includes the **mode**, the **model names** and a **hash of every prompt file**, so editing a prompt or switching models automatically skips summaries made under the old setup. A manual `PIPELINE_VERSION` covers code changes, such as extraction fixes.
- **The cache stores the Markdown summary,** never the uploaded file (the privacy promise) and not the PDF. Rebuilding the PDF takes milliseconds, so PDF layout fixes apply to old summaries, and each student's PDF shows their own file name.
- **One lock per key:** if two students click the same deck at the same moment, the second waits and then gets a hit, instead of both running the model for minutes. There's a test for this.
- **Atomic writes** (temp file + rename): a crash can't leave a half-written summary that later requests would serve.
- **Failures aren't cached**, so a model outage doesn't stick.
- The response has an `X-Cache: HIT|MISS` header, and the extension shows "⚡ (cached)".
- **Not yet:** cache expiry and size limits, and a shared cache across server instances (one folder on one server is enough until Stage 8 hosting).
