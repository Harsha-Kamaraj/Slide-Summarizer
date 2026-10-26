# Install Slide Summarizer (for students)

Turn any PESU Academy slide deck into a study-notes PDF in one click. You only need Google Chrome. Nothing else to install.

## 1. Install (2 minutes, once)

1. Download **`slide-summarizer-<version>.zip`** (from whoever shared it with you) and **unzip** it. You'll get a folder called **`slide-summarizer`**. Keep this folder somewhere it won't be deleted, e.g. Documents. Chrome runs the extension from it.
2. In Chrome, open **`chrome://extensions`** (type it into the address bar).
3. Turn on **Developer mode** (switch at the top right).
4. Click **Load unpacked** and choose the **`slide-summarizer`** folder.
5. Pin it: click the 🧩 puzzle icon in Chrome's toolbar, then the 📌 next to **Slide Summarizer**.

Chrome may show a notice about "developer mode extensions". That's normal for extensions installed this way.

## 2. Use it

1. Log in to **PESU Academy** → **My Courses** → a course → a unit → **Slides**.
2. Next to each deck there's a navy **✦ Summarize** button. Click it.
3. The button shows progress ("Summarizing part 2/5…"). Large decks, diagrams and AI rate limits can take several minutes. A cached deck skips AI processing, but still needs a file transfer to identify it.
4. The PDF is saved to your **Downloads** folder.
5. Optional: click 👍 or 👎 to say whether the summary helped.

**Options:** click the extension's icon in the toolbar to choose **Detailed** or **Short**, and to add **exam questions** or **flashcards**. You can also summarize a file from your computer there.

The first click after the server has been idle for a while can take about a minute longer while it wakes up.

## 3. If something goes wrong

| What you see | What to do |
|---|---|
| No ✦ Summarize buttons | Refresh the PESU page. Make sure you're on the **Slides** tab |
| "PESU didn't return a PDF. Are you still logged in?" | Log in to PESU again and refresh |
| "Can't reach the summarizer server" | Check your internet; try again in a minute (the server may be waking up) |
| "Connection interrupted. Reconnecting…" | The extension is checking the same job again; it does not re-upload the PDF |
| "Your job number is saved" | Click Retry to reconnect to that job. For a manually selected file, reopen the popup and click **Reconnect to saved summary** |
| "The server no longer has this job" | It expired or the server restarted. Retry starts a new job; completed cached summaries may still be reusable |
| "Couldn't download the PDF from PESU" | Open the slide's eye icon and check your PESU login and connection |
| "Processing smaller sections to fit the AI limit" | Let it continue; the server is splitting an oversized AI request |
| "You've reached today's limit…" | Each student gets 10 new summaries a day. Decks others already summarized still work |

**Updating:** when you get a new zip, unzip it over the old folder, then click ↻ on Slide Summarizer in `chrome://extensions`.

Version **0.4.0** requires Chrome 120 or newer. Refresh any already-open PESU pages after updating. Keep only one installed copy enabled. Jobs started by an older version cannot be recovered by the update because their job numbers were not saved.

New jobs save their server job number and filename locally in Chrome. Reopening the same slide with the same settings reconnects to that saved job. An upload interrupted before its job number is received cannot be resumed automatically. Closing Chrome pauses local checks and downloads; they resume when Chrome starts again, provided the server retains the job. Backend 0.5.0 saves completed job records for download recovery (available for one hour after completion by default). After a restart, an unfinished job asks for a retry with the same file/settings and reuses successfully saved AI sections. It does not continue automatically without the source file.

Backend 0.5.0 also includes an **Original Visual Slides** appendix with source slide numbers and captions. Detection is heuristic; it can include charts/tables or miss diagrams. Up to 40 detected teaching visuals are included by default, spread through the deck if capped. The PDF states how many were included. These are original images, not newly drawn diagrams. Existing extension 0.4.0 supports these backend features without reinstalling.

**Privacy:** your PESU password never leaves your browser. The slide file is deleted from the server right after it's summarized. See [PRIVACY.md](PRIVACY.md).
