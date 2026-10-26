// Where the summarizer backend runs: the hosted server by default (Render,
// docs/DEPLOY.md). For development, set http://localhost:8000 under ⚙ in the
// popup.
export const DEFAULT_BACKEND_URL = "https://slide-summarizer.onrender.com";

// Upload limit, matching MAX_UPLOAD_MB in backend/config.py.
export const MAX_UPLOAD_MB = 50;

// Settings saved in chrome.storage.sync, with their defaults.
export const DEFAULT_SETTINGS = {
  backendUrl: DEFAULT_BACKEND_URL,
  mode: "detailed", // "short" | "detailed"
  examQuestions: false,
  flashcards: false,
};

export async function getSettings() {
  return { ...DEFAULT_SETTINGS, ...(await chrome.storage.sync.get(DEFAULT_SETTINGS)) };
}
