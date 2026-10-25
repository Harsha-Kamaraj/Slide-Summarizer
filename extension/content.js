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
  button.disabled = true;
  button.textContent = "⏳ Summarizing… (a few minutes)";
  button.title = "";

  const result = await chrome.runtime
    .sendMessage({ type: "summarize-url", url: url.href, title })
    .catch((error) => ({ ok: false, error: error.message }));

  button.disabled = false;
  if (result?.ok) {
    button.textContent = result.cached ? "⚡ Saved to Downloads (cached)" : "✅ Saved to Downloads";
  } else {
    button.textContent = "⚠️ Failed: click to retry";
    button.title = result?.error || "Unknown error";
    alert(`Slide Summarizer: ${button.title}`);
  }
}

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
