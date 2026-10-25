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

function addButtons() {
  for (const link of document.querySelectorAll(`${SLIDE_LINK}:not([${MARK}])`)) {
    const id = link.getAttribute("onclick").match(ID_PATTERN)?.[1];
    if (!id) continue;
    link.setAttribute(MARK, "1");

    const button = document.createElement("button");
    button.type = "button";
    button.className = "slide-summarizer-btn";
    button.textContent = "📝 Summarize";
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
  button.disabled = true;
  button.textContent = "⏳ Uploading…";
  button.title = "";
  // Re-summarizing: remove the previous run's feedback buttons.
  const oldFeedback = button.nextElementSibling;
  if (oldFeedback?.classList.contains("slide-summarizer-feedback")) oldFeedback.remove();
  pending.set(requestId, button);

  const result = await chrome.runtime
    .sendMessage({ type: "summarize-url", url: url.href, title, requestId })
    .catch((error) => ({ ok: false, error: error.message }));
  pending.delete(requestId);

  button.disabled = false;
  if (result?.ok) {
    button.textContent = result.cached ? "⚡ Saved to Downloads (cached)" : "✅ Saved to Downloads";
    if (result.summaryId) button.after(feedbackButtons(result.summaryId));
  } else {
    button.textContent = "⚠️ Failed: click to retry";
    button.title = result?.error || "Unknown error";
    alert(`Slide Summarizer: ${button.title}`);
  }
}

function feedbackButtons(summaryId) {
  const box = document.createElement("span");
  box.className = "slide-summarizer-feedback";
  box.append("Helpful? ");
  for (const [emoji, rating] of [["👍", "up"], ["👎", "down"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = emoji;
    button.title = rating === "up" ? "This summary was helpful" : "This summary wasn't helpful";
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      box.textContent = "Thanks!";
      await chrome.runtime.sendMessage({ type: "feedback", summaryId, rating }).catch(() => {});
    });
    box.append(button);
  }
  return box;
}

chrome.runtime.onMessage.addListener((message) => {
  if (message.type !== "summarize-progress") return;
  const button = pending.get(message.requestId);
  if (button && message.text) button.textContent = `⏳ ${message.text}`;
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
