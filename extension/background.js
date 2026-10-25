// Background service worker: does the actual work for both entry points.
//   - content.js sends {type: "summarize-url"} when a student clicks a
//     Summarize button on a PESU slides page.
//   - popup.js sends {type: "summarize-file"} for a file picked by hand.
// Doing the work here (not in the page or popup) means it keeps going if
// the student closes the popup, and it can use our host permissions.

import { MAX_UPLOAD_MB, getSettings } from "./config.js";

const POLL_INTERVAL_MS = 2000;

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const handlers = {
    "summarize-url": summarizeFromUrl,
    "summarize-file": summarizeFromFile,
    feedback: sendFeedback,
  };
  const handler = handlers[message.type];
  if (!handler) return false;

  handler(message, sender)
    .then((result) => sendResponse({ ok: true, ...result }))
    .catch((error) => {
      console.error(`${message.type} failed:`, error);
      sendResponse({ ok: false, error: error.message });
    });
  return true; // keep the message channel open for the async response
});

async function summarizeFromUrl({ url, title, requestId }, sender) {
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
  // Progress goes back to the tab that asked, so its button can show it.
  const onProgress = (text) =>
    chrome.tabs
      .sendMessage(sender.tab.id, { type: "summarize-progress", requestId, text })
      .catch(() => {});
  return summarizeBlob(blob, `${title}.pdf`, onProgress);
}

async function summarizeFromFile({ name, dataBase64, mimeType }) {
  // Messages between extension pages can't carry binary data, so the popup
  // sends the file base64-encoded.
  const bytes = Uint8Array.from(atob(dataBase64), (c) => c.charCodeAt(0));
  // The popup may have been closed; then nobody is listening, which is fine.
  const onProgress = (text) =>
    chrome.runtime
      .sendMessage({ type: "summarize-progress", requestId: "popup", text })
      .catch(() => {});
  return summarizeBlob(new Blob([bytes], { type: mimeType }), name, onProgress);
}

async function summarizeBlob(blob, filename, onProgress) {
  if (blob.size > MAX_UPLOAD_MB * 1024 * 1024) {
    throw new Error(`This file is larger than the ${MAX_UPLOAD_MB} MB limit.`);
  }
  const { backendUrl, mode, examQuestions, flashcards } = await getSettings();
  const form = new FormData();
  form.append("file", blob, filename);
  form.append("mode", mode);
  if (examQuestions) form.append("extras", "exam_questions");
  if (flashcards) form.append("extras", "flashcards");

  setBadge("…", "#1f4e79");
  const stopKeepAlive = keepServiceWorkerAlive();
  try {
    // Submit a job, then poll it: no single request has to stay open for
    // the minutes a summary can take, and we get progress text to show.
    const { job_id: jobId } = await api(backendUrl, "/jobs", { method: "POST", body: form });
    let status;
    do {
      await sleep(POLL_INTERVAL_MS);
      status = await api(backendUrl, `/jobs/${jobId}`);
      onProgress(status.progress);
    } while (status.status === "queued" || status.status === "running");
    if (status.status === "error") throw new Error(status.error);

    const pdf = await fetchOk(`${backendUrl}/jobs/${jobId}/pdf`);
    await downloadPdf(await pdf.blob(), `${stem(filename)}_summary.pdf`);
    setBadge("✓", "#2e7d32");
    return { cached: status.cached, summaryId: status.summary_id };
  } catch (error) {
    setBadge("!", "#c62828");
    throw error;
  } finally {
    stopKeepAlive();
  }
}

async function sendFeedback({ summaryId, rating }) {
  const { backendUrl } = await getSettings();
  await fetchOk(`${backendUrl}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ summary_id: summaryId, rating }),
  });
  return {};
}

async function api(backendUrl, path, options) {
  return (await fetchOk(`${backendUrl}${path}`, options)).json();
}

async function fetchOk(url, options) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error(`Can't reach the summarizer at ${new URL(url).origin}. Is the backend running?`);
  }
  if (!response.ok) {
    // The backend returns {"detail": "..."} messages written for students.
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
  return response;
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
  // Chrome stops an idle extension service worker after ~30 s. Polling uses
  // fetch, which doesn't count as activity, so also make a cheap extension
  // API call every 20 s, which resets the idle timer.
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function stem(filename) {
  return filename.replace(/\.[^.]+$/, "");
}

function safeFilename(name) {
  // Characters that aren't allowed in file names on Windows/macOS.
  return name.replace(/[\\/:*?"<>|]+/g, "_").trim();
}
