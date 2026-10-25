# Slide Summarizer — Privacy Policy

_Last updated: 25 October 2026_

Slide Summarizer is a Chrome extension that turns lecture slides into a PDF summary. This page explains exactly what data it handles.

## What happens when you click "Summarize"

1. **The extension downloads the slides inside your browser**, using your existing PESU Academy session. Your PESU login, password and cookies are **never sent to our server**. They stay in your browser.
2. **The slide file is uploaded to the Slide Summarizer server** to be summarized. The server keeps it only while it's being processed and **deletes it as soon as the summary is made**.
3. **The text of the slides (and images of diagram-heavy slides) is sent to an AI service** that writes the summary. In development this runs on the developer's own computer; the hosted version uses a third-party AI provider, whose own privacy policy applies to that request.
4. **The finished summary is downloaded to your computer.**

## What the server keeps

| Data | Kept? | Why |
|---|---|---|
| The slide file you upload | **No.** Deleted right after processing | — |
| The generated summary text | Yes, in a cache, identified only by a fingerprint (hash) of the file | So the next student who summarizes the same lecture gets it instantly, without another AI request |
| Your IP address | Only in memory, for the current day, never written to disk | To enforce a daily limit on new summaries per student |
| 👍/👎 feedback | Yes: the rating, an optional comment you type, and which summary it's about | To improve the summaries |
| Your name, email, student ID or account | **Never collected** | — |

There are no accounts, no tracking, no analytics and no advertising.

## What the extension stores in your browser

Your settings (summary length, extras, backend address) are saved with Chrome's sync storage, so they follow your Chrome profile. Nothing else is stored.

## Permissions the extension asks for

- **Read pesuacademy.com pages**, to add the Summarize buttons next to slide decks.
- **files.pesuacademy.com**, to download the slide file you chose.
- **The Slide Summarizer server**, to upload the file and get the summary back.
- **Downloads**, to save the summary PDF.
- **Storage**, to remember your settings.

## Contact

Questions or requests to delete feedback: open an issue at <https://github.com/Harsha-Kamaraj/Slide-Summarizer/issues>.
