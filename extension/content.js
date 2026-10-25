// Content script: runs inside PESU Academy pages and adds a "Summarize"
// button next to every slide deck link.
//
// A slide link on the page looks like:
//   <a onclick="loadIframe('/Academy/a/referenceMeterials/downloadslidecoursedoc/<id>#view=...', '<id>')">
//     ML models <span class="glyphicon glyphicon-eye-open"></span>
//   </a>
// GET .../downloadslidecoursedoc/<id> redirects to a signed PDF link, so the
// id is all we need. (Content scripts can't be ES modules, hence no imports.)

const SLIDE_LINK = 'a[onclick*="downloadslidecoursedoc/"]';
const ID_PATTERN = /downloadslidecoursedoc\/([0-9a-f-]{36})/i;
const MARK = "data-slide-summarizer"; // set on links that already have a button

// Buttons waiting on a summary, by request id, so progress updates from the
// background worker reach the right button.
const pending = new Map();

// Icons are fixed markup (no user data), so setting them as HTML is safe.
const ICONS = {
  idle: '<svg class="ss-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M11 2l2 6.5L19.5 10 13 12l-2 6.5L9 12 2.5 10 9 8.5z"/><path d="M19 14l1 2.6 2.6 1-2.6 1L19 21.2l-1-2.6-2.6-1 2.6-1z"/></svg>',
  busy: '<span class="ss-spinner" aria-hidden="true"></span>',
  done: '<svg class="ss-icon" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  error: '<svg class="ss-icon" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M12 7v6M12 17h.01"/></svg>',
};

function setState(button, state, text) {
  button.className = `ss-btn ${state === "done" ? "ss-done" : state === "error" ? "ss-error" : ""}`;
  button.disabled = state === "busy";
  button.innerHTML = ICONS[state];
  const label = document.createElement("span");
  label.textContent = text;
  button.append(label);
}

function addButtons() {
  for (const link of document.querySelectorAll(`${SLIDE_LINK}:not([${MARK}])`)) {
    const id = link.getAttribute("onclick").match(ID_PATTERN)?.[1];
    if (!id) continue;
    link.setAttribute(MARK, "1");

    const button = document.createElement("button");
    button.type = "button";
    setState(button, "idle", "Summarize");
    button.addEventListener("click", (event) => {
      // The button sits next to PESU's link; don't let the click reach it.
      event.preventDefault();
      event.stopPropagation();
      summarize(button, id, link.textContent.trim() || "slides");
    });
    // After the link, not inside it: a click inside would also open PESU's viewer.
    link.after(button);
  }
}

async function summarize(button, id, title) {
  const url = new URL(`/Academy/a/referenceMeterials/downloadslidecoursedoc/${id}`, location.origin);
  const requestId = crypto.randomUUID();
  setState(button, "busy", "Uploading…");
  button.title = "";
  // Re-summarizing: remove the previous run's feedback or error note.
  const old = button.nextElementSibling;
  if (old?.classList.contains("ss-feedback")) old.remove();
  pending.set(requestId, button);

  const result = await chrome.runtime
    .sendMessage({ type: "summarize-url", url: url.href, title, requestId })
    .catch((error) => ({ ok: false, error: error.message }));
  pending.delete(requestId);

  if (result?.ok) {
    setState(button, "done", result.cached ? "Saved · instant" : "Saved to Downloads");
    if (result.summaryId) button.after(feedbackButtons(result.summaryId));
  } else {
    setState(button, "error", "Retry");
    button.title = result?.error || "Unknown error";
    button.after(note(button.title));
  }
}

function note(text) {
  const span = document.createElement("span");
  span.className = "ss-feedback ss-note-error";
  span.textContent = text;
  return span;
}

function feedbackButtons(summaryId) {
  const box = document.createElement("span");
  box.className = "ss-feedback";
  box.append("Helpful?");
  for (const [emoji, rating] of [["👍", "up"], ["👎", "down"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = emoji;
    button.title = rating === "up" ? "This summary was helpful" : "This summary wasn't helpful";
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      box.textContent = "Thanks for the feedback";
      await chrome.runtime.sendMessage({ type: "feedback", summaryId, rating }).catch(() => {});
    });
    box.append(button);
  }
  return box;
}

chrome.runtime.onMessage.addListener((message) => {
  if (message.type !== "summarize-progress") return;
  const button = pending.get(message.requestId);
  if (button && message.text) setState(button, "busy", message.text);
});

// PESU is a single-page app: opening the Slides tab changes the page without
// a reload, so watch for new content instead of scanning only once.
let scheduled = false;
new MutationObserver(() => {
  if (scheduled) return;
  scheduled = true;
  // Batch bursts of DOM changes into one scan per animation frame.
  requestAnimationFrame(() => {
    scheduled = false;
    addButtons();
  });
}).observe(document.body, { childList: true, subtree: true });

addButtons();
