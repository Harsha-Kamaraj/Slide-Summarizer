// Popup: settings, backend status, and summarizing a file picked by hand.

import { DEFAULT_BACKEND_URL, MAX_UPLOAD_MB, getSettings } from "./config.js";

const ALLOWED = [".pdf", ".pptx", ".ppt"];
const MODE_HINTS = {
  detailed: "Explained bullets, boxed formulas and worked examples.",
  short: "The key ideas only, for a quick revision.",
};

const alertBox = document.getElementById("alert");
const modeControl = document.getElementById("mode");
const modeHint = document.getElementById("modeHint");
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file");
const fileLabel = document.getElementById("fileLabel");
const summarizeButton = document.getElementById("summarize");
const message = document.getElementById("message");
const settingsToggle = document.getElementById("settingsToggle");
const settingsPanel = document.getElementById("settings");
const backendInput = document.getElementById("backendUrl");

let selectedFile = null;

async function init() {
  const settings = await getSettings();
  showMode(settings.mode);
  for (const id of ["examQuestions", "flashcards"]) {
    const box = document.getElementById(id);
    box.checked = settings[id];
    box.addEventListener("change", () => chrome.storage.sync.set({ [id]: box.checked }));
  }
  backendInput.value = settings.backendUrl;
  checkBackend(settings.backendUrl);
}

// --- Server check (a warning appears only if something is wrong) ----------

function setAlert(kind, text) {
  alertBox.hidden = !text;
  alertBox.className = `alert ${kind}`;
  alertBox.textContent = text;
}

async function checkBackend(backendUrl) {
  try {
    const health = await (await fetch(`${backendUrl}/health`)).json();
    if (health.ollama) setAlert("", "");
    else setAlert("warn", "The summarizer is up, but its AI model isn't reachable right now.");
  } catch {
    setAlert("bad", "Can't reach the summarizer server. Check your internet, or the server address in ⚙.");
  }
}

// --- Summary length (segmented control) -------------------------------------

function showMode(mode) {
  modeControl.dataset.mode = mode;
  for (const button of modeControl.querySelectorAll("button")) {
    button.setAttribute("aria-checked", String(button.dataset.mode === mode));
  }
  modeHint.textContent = MODE_HINTS[mode];
}

modeControl.addEventListener("click", (event) => {
  const mode = event.target.closest("button")?.dataset.mode;
  if (!mode) return;
  showMode(mode);
  chrome.storage.sync.set({ mode });
});

// --- Picking a file (browse or drag and drop) --------------------------------

function selectFile(file) {
  if (!file) return;
  const ok = ALLOWED.some((ext) => file.name.toLowerCase().endsWith(ext));
  if (!ok) {
    showMessage("error", "Please choose a .pdf, .pptx or .ppt file.");
    return;
  }
  selectedFile = file;
  fileLabel.textContent = file.name;
  dropzone.classList.add("has-file");
  summarizeButton.disabled = false;
  showMessage("", "");
}

fileInput.addEventListener("change", () => selectFile(fileInput.files[0]));

for (const type of ["dragenter", "dragover"]) {
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.add("over");
  });
}
for (const type of ["dragleave", "drop"]) {
  dropzone.addEventListener(type, () => dropzone.classList.remove("over"));
}
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  selectFile(event.dataTransfer.files[0]);
});

// --- Summarize ---------------------------------------------------------------

function showMessage(kind, text, { busy = false } = {}) {
  message.className = `message ${kind}`;
  message.replaceChildren();
  if (!text) return;
  if (busy) {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    message.append(spinner);
  }
  const span = document.createElement("span");
  span.textContent = text;
  message.append(span);
}

summarizeButton.addEventListener("click", async () => {
  const file = selectedFile;
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    showMessage("error", `This file is larger than the ${MAX_UPLOAD_MB} MB limit.`);
    return;
  }
  summarizeButton.disabled = true;
  showMessage("", "Summarizing… you can close this popup; the PDF will appear in Downloads.", {
    busy: true,
  });

  // The background worker does the upload, so it keeps going even if this
  // popup closes. Messages can't carry binary data, so send base64.
  const result = await chrome.runtime
    .sendMessage({
      type: "summarize-file",
      name: file.name,
      mimeType: file.type || "application/octet-stream",
      dataBase64: await fileToBase64(file),
    })
    .catch((error) => ({ ok: false, error: error.message }));

  summarizeButton.disabled = false;
  if (result?.ok) {
    showMessage("", `Saved to Downloads${result.cached ? " · from cache" : ""}.`);
  } else {
    showMessage("error", result?.error || "Something went wrong.");
  }
});

// Progress for a file summarized from this popup (while it stays open).
chrome.runtime.onMessage.addListener((update) => {
  if (update.type === "summarize-progress" && update.requestId === "popup" && update.text) {
    showMessage("", update.text, { busy: true });
  }
});

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    // Result is "data:<type>;base64,<data>"; keep only the data part.
    reader.onload = () => resolve(reader.result.split(",", 2)[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

// --- Settings ----------------------------------------------------------------

settingsToggle.addEventListener("click", () => {
  const open = settingsPanel.hidden;
  settingsPanel.hidden = !open;
  settingsToggle.setAttribute("aria-expanded", String(open));
});

document.getElementById("saveBackend").addEventListener("click", async () => {
  const backendUrl = backendInput.value.trim().replace(/\/+$/, "") || DEFAULT_BACKEND_URL;
  backendInput.value = backendUrl;
  // A new server needs permission before the extension may contact it.
  // Chrome only shows this prompt from a click, hence the Save button.
  const granted = await chrome.permissions.request({ origins: [`${new URL(backendUrl).origin}/*`] });
  if (!granted) {
    setAlert("bad", "Permission denied: the extension can't contact that server.");
    return;
  }
  await chrome.storage.sync.set({ backendUrl });
  checkBackend(backendUrl);
});

init();
