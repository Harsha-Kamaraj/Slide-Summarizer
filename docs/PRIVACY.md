# Slide Summarizer — Privacy Policy

_Last updated: 26 October 2026_

Slide Summarizer is a Chrome extension that turns lecture slides into a PDF summary. This page explains exactly what data it handles.

## What happens when you click "Summarize"

1. **The extension downloads the slides inside your browser**, using your existing PESU Academy session. Your PESU login, password and cookies are **never sent to our server**. They stay in your browser.
2. **The slide file is uploaded to the Slide Summarizer server** to be summarized. The server keeps it only while it's being processed and **deletes it as soon as the summary is made**.
3. **The text of the slides (and images of diagram-heavy slides and selected topic-diagram candidates) is sent to an AI service** that writes the summary. In development this runs on the developer's own computer; the hosted version uses a third-party AI provider, whose own privacy policy applies to that request.
4. **The finished summary is downloaded to your computer.**

## What the server keeps

| Data | Kept? | Why |
|---|---|---|
| The slide file you upload | **No.** Deleted right after processing | — |
| Summary, questions, flashcards and intermediate AI responses | Yes, in a cache keyed by content, settings and prompt/model fingerprints | Reuse finished work without repeating AI requests |
| Diagram data | Yes, cropped source images, validated graph descriptions and captions in the cache; older full-slide cache entries may remain | Place diagrams with their summary topics; disable with `INCLUDE_DIAGRAMS=0` |
| Server job records | Yes: random job number, filename, timestamps, outcome and result | Recover completed downloads after restart and identify interrupted jobs |
| Your IP address | Only in memory, for the current day, never written to disk | To enforce a daily limit on new summaries per student |
| 👍/👎 feedback | Yes: the rating, an optional comment you type, and which summary it's about | To improve the summaries |
| Your PESU account details | Not requested from the browser; names/emails already printed in uploaded slides can appear in source images or generated text | No PESU account integration |

There are no accounts, no tracking, no analytics and no advertising.

When configured, cache entries are copied to a private Hugging Face dataset. They remain until the operator deletes them; the job download expiry (one hour after completion by default) limits API access, not storage retention. Dataset history can retain previous versions. Only hash keys and responses are cached for intermediate AI calls, not API credentials or full request prompts. A failed remote save is logged; recovery after disk loss then depends on which entries reached the dataset. The backend does not keep the full uploaded deck after processing, but selected visual pages can reproduce part of that deck.

## What the extension stores in your browser

Your settings (summary length, extras, backend address) are saved with Chrome's sync storage, so they follow your Chrome profile.

To reconnect after an interruption, the extension also saves job numbers, filenames, status/error messages, backend addresses, download IDs, and fingerprints identifying the selected file/link and settings in Chrome's local storage. These job records stay on that browser profile and are not synced to your other devices. The extension does not save the original slide file, signed download links, or PESU cookies in these records. Removing the extension clears its local records; downloaded PDFs remain in your Downloads folder.

## Permissions the extension asks for

- **Read pesuacademy.com pages**, to add the Summarize buttons next to slide decks.
- **files.pesuacademy.com**, to download the slide file you chose.
- **The Slide Summarizer server**, to upload the file and get the summary back.
- **Downloads**, to save the summary PDF.
- **Storage**, to remember your settings and reconnect to saved jobs.
- **Alarms**, to check saved jobs periodically while Chrome is running, even when the popup is closed.

## Contact

Questions or requests to delete feedback: open an issue at <https://github.com/Harsha-Kamaraj/Slide-Summarizer/issues>.
