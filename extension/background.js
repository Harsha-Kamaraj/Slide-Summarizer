// Requests end after upload. Job IDs survive worker/browser restarts;
// short checks and alarms take over processing and downloading afterwards.
import { MAX_UPLOAD_MB, getSettings } from "./config.js";
import { fetchOk, readJob } from "./backend-api.js";
import { JobTracker, TASK_PREFIX, ACTIVE_STATES, taskKey } from "./job-tracker.js";

const ALARM = "check-summaries";
const storage = {
  get: async (key) => (await chrome.storage.local.get(key))[key],
  set: async (key, value) => chrome.storage.local.set({ [key]: value }),
  all: async () => Object.entries(await chrome.storage.local.get(null))
    .filter(([key]) => key.startsWith(TASK_PREFIX)).map(([, value]) => value),
};
const tracker = new JobTracker({
  storage, readJob, download: downloadPdf,
  cancelJob: async (url) => {
    const response = await fetchOk(url, { method: "POST", signal: AbortSignal.timeout(20000) });
    return response.json();
  },
  getDownload: async (id) => (await chrome.downloads.search({ id }))[0],
  onChange: (task) => {
    const text = task.state === "done" ? "✓" : ACTIVE_STATES.includes(task.state) ? "…" : "!";
    chrome.action.setBadgeText({ text });
    chrome.action.setBadgeBackgroundColor({ color: text === "!" ? "#c62828" : "#1f4e79" });
  },
});

const backgroundCheck = (key) => tracker.check(key).catch((error) => console.error("Job check:", error));
async function ensureAlarm() {
  if (!await chrome.alarms.get(ALARM)) await chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
}
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) tracker.checkActive().catch(console.error);
});
chrome.runtime.onStartup.addListener(() => {
  ensureAlarm().then(() => tracker.checkActive()).catch(console.error);
});
chrome.runtime.onInstalled.addListener(() => {
  ensureAlarm().then(() => tracker.checkActive()).catch(console.error);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const handlers = {
    "summarize-url": summarizeFromUrl,
    "summarize-file": summarizeFromFile,
    "get-url-task": getUrlTask,
    "get-task": getTask,
    "resume-task": resumeTask,
    "cancel-task": async ({ key }) => ({ task: await tracker.cancel(key) }),
    feedback: sendFeedback,
  };
  if (!handlers[message.type]) return false;
  handlers[message.type](message, sender)
    .then((result) => sendResponse({ ok: true, ...result }))
    .catch((error) => {
      console.error(`${message.type} failed:`, error);
      sendResponse({ ok: false, error: error.message });
    });
  return true;
});

async function settingsForTask() {
  const settings = await getSettings();
  settings.backendUrl = settings.backendUrl.replace(/\/+$/, "");
  return settings;
}

function pesuSource(url) {
  const parsed = new URL(url);
  if (!["pesuacademy.com", "www.pesuacademy.com"].includes(parsed.hostname) ||
      parsed.protocol !== "https:" ||
      !/^\/Academy\/a\/referenceMeterials\/downloadslidecoursedoc\/[0-9a-f-]{36}$/i.test(parsed.pathname)) {
    throw new Error("This is not a supported PESU slide link.");
  }
  // No signed URLs, cookies, or original files are saved in local storage.
  return parsed.pathname;
}

async function getUrlTask({ url }) {
  const key = await taskKey(pesuSource(url), await settingsForTask());
  return getTask({ key });
}

async function getTask({ key }) {
  await ensureAlarm();
  const task = await tracker.get(key);
  if (task && ACTIVE_STATES.includes(task.state)) void backgroundCheck(key);
  return { task: task || null };
}

async function resumeTask({ key }) {
  const task = await tracker.get(key);
  if (!task?.jobId || ["failed", "expired", "cancelled"].includes(task.state)) {
    throw new Error("Choose the file again to start a new summary.");
  }
  await ensureAlarm();
  const saved = await tracker.start(task, () => { throw new Error("No saved job to resume."); });
  void backgroundCheck(key);
  return { task: saved };
}

async function summarizeFromUrl({ url, title, requestId }, sender) {
  const settings = await settingsForTask();
  const key = await taskKey(pesuSource(url), settings);
  const notify = (text) => chrome.tabs.sendMessage(sender.tab.id,
    { type: "summarize-progress", requestId, text }).catch(() => {});
  return startSummary(key, `${title || "slides"}.pdf`, settings, async () => {
    notify("Downloading slides from PESU…");
    let response, blob;
    try {
      response = await fetch(url, { credentials: "include", signal: AbortSignal.timeout(25000) });
      if (response.ok) blob = await response.blob();
    } catch (cause) {
      throw new Error("Couldn't download the PDF from PESU. Open its eye icon to check your login " +
        "and connection, then try again. The file has not been uploaded to the summarizer.", { cause });
    }
    if (!response.ok) throw new Error(`PESU slide download failed (HTTP ${response.status}).`);
    if (!(response.headers.get("content-type") || "").includes("pdf")) {
      throw new Error("PESU returned a page instead of a PDF. Log in again and reopen the slide.");
    }
    return blob;
  }, notify);
}

async function summarizeFromFile({ name, dataBase64, mimeType }) {
  const bytes = Uint8Array.from(atob(dataBase64), (c) => c.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hash = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  const settings = await settingsForTask();
  const key = await taskKey(`file:${hash}`, settings);
  await chrome.storage.local.set({ lastPopupTask: key });
  return startSummary(key, name, settings, async () => new Blob([bytes], { type: mimeType }),
    (text) => chrome.runtime.sendMessage({ type: "summarize-progress", requestId: "popup", text }).catch(() => {}));
}

async function startSummary(key, filename, settings, loadBlob, notify) {
  await ensureAlarm();
  // Explicitly summarizing a finished deck must reach the current backend
  // pipeline. It reuses cached text/extras while upgrading old diagrams.
  const task = await tracker.start({ key, filename, backendUrl: settings.backendUrl, refreshCompleted: true }, async (progress) => {
    await progress("Connecting to the summarizer…");
    notify("Connecting to the summarizer…");
    // Waking a sleeping host is a safe GET. Do this before transferring the
    // lecture, so a cold start doesn't strand a non-idempotent POST upload.
    try {
      const health = await readJob(`${settings.backendUrl}/health`, "json", async () => {
        await progress("Waiting for the summarizer to wake up…");
        notify("Waiting for the summarizer to wake up…");
      });
      if (health.status !== "ok") throw new Error("Unexpected health response");
    } catch (cause) {
      throw new Error("Couldn't connect to the summarizer. No PDF was uploaded. " +
        "Check your connection and server address, then retry.", { cause });
    }
    await progress("Downloading slides…");
    const blob = await loadBlob();
    if (blob.size > MAX_UPLOAD_MB * 1024 * 1024) {
      throw new Error(`This file is larger than the ${MAX_UPLOAD_MB} MB limit.`);
    }
    await progress("Uploading PDF to the summarizer…");
    notify("Uploading PDF to the summarizer…");
    const form = new FormData();
    form.append("file", blob, filename);
    form.append("mode", settings.mode);
    if (settings.examQuestions) form.append("extras", "exam_questions");
    if (settings.flashcards) form.append("extras", "flashcards");
    try {
      const response = await fetchOk(`${settings.backendUrl}/jobs`, {
        method: "POST", body: form, signal: AbortSignal.timeout(25000),
      });
      return (await response.json()).job_id;
    } catch (cause) {
      if (cause.status) throw cause; // keep validation and quota messages
      throw new Error("Couldn't confirm the upload. The server may have received the file. " +
        "Check your connection before trying again.", { cause });
    }
  });
  if (task.jobId && ACTIVE_STATES.includes(task.state)) void backgroundCheck(key);
  return { task };
}

async function sendFeedback({ summaryId, rating }) {
  const { backendUrl } = await settingsForTask();
  await fetchOk(`${backendUrl}/feedback`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ summary_id: summaryId, rating }), signal: AbortSignal.timeout(20000),
  });
  return {};
}

async function downloadPdf(blob, filename) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  const name = `${filename.replace(/\.[^.]+$/, "")}_summary.pdf`.replace(/[\\/:*?"<>|]+/g, "_").trim();
  return chrome.downloads.download({ url: `data:application/pdf;base64,${btoa(binary)}`, filename: name });
}
