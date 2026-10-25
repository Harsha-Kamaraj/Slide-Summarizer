// Popup: settings, backend status, and summarizing a file picked by hand.

import { DEFAULT_BACKEND_URL, MAX_UPLOAD_MB, getSettings } from "./config.js";

const statusBox = document.getElementById("status");
const fileInput = document.getElementById("file");
const summarizeButton = document.getElementById("summarize");
const message = document.getElementById("message");
const backendInput = document.getElementById("backendUrl");

async function init() {
  const settings = await getSettings();
  document.querySelector(`input[name="mode"][value="${settings.mode}"]`).checked = true;
  backendInput.value = settings.backendUrl;
  checkBackend(settings.backendUrl);
}

async function checkBackend(backendUrl) {
  statusBox.className = "status";
  statusBox.textContent = "Checking backend…";
  try {
    const health = await (await fetch(`${backendUrl}/health`)).json();
    if (health.ollama) {
      statusBox.className = "status ok";
      statusBox.textContent = "✓ Ready";
    } else {
      statusBox.className = "status bad";
      statusBox.textContent = "Backend is up, but the AI model isn't reachable.";
    }
  } catch {
    statusBox.className = "status bad";
    statusBox.textContent = `Can't reach the backend at ${backendUrl}.`;
  }
}

for (const radio of document.querySelectorAll('input[name="mode"]')) {
  radio.addEventListener("change", () => chrome.storage.sync.set({ mode: radio.value }));
}

backendInput.addEventListener("change", async () => {
  const backendUrl = backendInput.value.trim().replace(/\/+$/, "") || DEFAULT_BACKEND_URL;
  backendInput.value = backendUrl;
  await chrome.storage.sync.set({ backendUrl });
  checkBackend(backendUrl);
});

fileInput.addEventListener("change", () => {
  summarizeButton.disabled = fileInput.files.length === 0;
  message.textContent = "";
});

summarizeButton.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    message.textContent = `⚠️ This file is larger than the ${MAX_UPLOAD_MB} MB limit.`;
    return;
  }
  summarizeButton.disabled = true;
  message.textContent =
    "⏳ Summarizing… This takes a few minutes. You can close this popup; " +
    "the PDF will appear in your Downloads.";

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
  message.textContent = result?.ok ? "✅ Saved to Downloads." : `⚠️ ${result?.error || "Failed."}`;
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

init();
