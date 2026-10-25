// Background service worker: does the actual work for both entry points.
//   - content.js sends {type: "summarize-url"} when a student clicks a
//     Summarize button on a PESU slides page.
//   - popup.js sends {type: "summarize-file"} for a file picked by hand.
// Doing the work here (not in the page or popup) means it keeps going if
// the student closes the popup, and it can use our host permissions.

import { MAX_UPLOAD_MB, getSettings } from "./config.js";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handlers = { "summarize-url": summarizeFromUrl, "summarize-file": summarizeFromFile };
  const handler = handlers[message.type];
  if (!handler) return false;

  handler(message)
    .then(() => sendResponse({ ok: true }))
    .catch((error) => {
      console.error("Summarize failed:", error);
      sendResponse({ ok: false, error: error.message });
    });
  return true; // keep the message channel open for the async response
});

async function summarizeFromUrl({ url, title }) {
  // The extension fetches the slides itself, inside the student's browser,
  // so their PESU login cookies apply. PESU answers with a redirect to a
  // short-lived signed file link, which fetch follows automatically.
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) {
    throw new Error(`Couldn't download the slides from PESU (HTTP ${response.status}).`);
  }
  // An expired session gets a login page (HTML) instead of a PDF.
  const type = response.headers.get("content-type") || "";
  if (!type.includes("pdf")) {
    throw new Error("PESU didn't return a PDF. Are you still logged in? Try reloading the page.");
  }
  const blob = await response.blob();
  await summarizeBlob(blob, `${title}.pdf`);
}

async function summarizeFromFile({ name, dataBase64, mimeType }) {
  // Messages between extension pages can't carry binary data, so the popup
  // sends the file base64-encoded.
  const bytes = Uint8Array.from(atob(dataBase64), (c) => c.charCodeAt(0));
  await summarizeBlob(new Blob([bytes], { type: mimeType }), name);
}

async function summarizeBlob(blob, filename) {
  if (blob.size > MAX_UPLOAD_MB * 1024 * 1024) {
    throw new Error(`This file is larger than the ${MAX_UPLOAD_MB} MB limit.`);
  }
  const { backendUrl, mode } = await getSettings();
  const form = new FormData();
  form.append("file", blob, filename);
  form.append("mode", mode);

  setBadge("…", "#1f4e79");
  const stopKeepAlive = keepServiceWorkerAlive();
  try {
    let response;
    try {
      response = await fetch(`${backendUrl}/summarize`, { method: "POST", body: form });
    } catch {
      throw new Error(`Can't reach the summarizer at ${backendUrl}. Is the backend running?`);
    }
    if (!response.ok) {
      // The backend returns {"detail": "..."} messages written for students.
      const body = await response.json().catch(() => ({}));
      const detail = typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`;
      throw new Error(detail);
    }
    const pdf = await response.blob();
    await downloadPdf(pdf, `${stem(filename)}_summary.pdf`);
    setBadge("✓", "#2e7d32");
  } catch (error) {
    setBadge("!", "#c62828");
    throw error;
  } finally {
    stopKeepAlive();
  }
}

async function downloadPdf(blob, filename) {
  // Service workers have no URL.createObjectURL, so hand the PDF to the
  // downloads API as a data: URL instead.
  const base64 = await blobToBase64(blob);
  await chrome.downloads.download({
    url: `data:application/pdf;base64,${base64}`,
    filename: safeFilename(filename),
  });
}

function keepServiceWorkerAlive() {
  // Chrome stops an idle extension service worker after ~30 s, even while a
  // fetch is waiting. Summaries take minutes on a laptop, so make a cheap
  // extension API call every 20 s, which resets the idle timer.
  // (Stage 8 replaces the one long request with job polling.)
  const timer = setInterval(() => chrome.runtime.getPlatformInfo(), 20_000);
  return () => clearInterval(timer);
}

async function blobToBase64(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  // Build the string in chunks: spreading a multi-MB array into one
  // String.fromCharCode call overflows the call stack.
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
  if (text === "✓") setTimeout(() => chrome.action.setBadgeText({ text: "" }), 10_000);
}

function stem(filename) {
  return filename.replace(/\.[^.]+$/, "");
}

function safeFilename(name) {
  // Characters that aren't allowed in file names on Windows/macOS.
  return name.replace(/[\\/:*?"<>|]+/g, "_").trim();
}
