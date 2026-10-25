// Where the summarizer backend runs. localhost for development; in Stage 8
// this becomes the hosted URL (still overridable from the popup settings).
export const DEFAULT_BACKEND_URL = "http://localhost:8000";

// Upload limit, matching MAX_UPLOAD_MB in backend/config.py.
export const MAX_UPLOAD_MB = 50;

// Settings saved in chrome.storage.sync, with their defaults.
export const DEFAULT_SETTINGS = {
  backendUrl: DEFAULT_BACKEND_URL,
  mode: "detailed", // "short" | "detailed"
};

export async function getSettings() {
  return { ...DEFAULT_SETTINGS, ...(await chrome.storage.sync.get(DEFAULT_SETTINGS)) };
}
