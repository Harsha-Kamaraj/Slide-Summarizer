import assert from "node:assert/strict";
import { test, mock } from "node:test";

// Exercise the real extension message handlers, persistence adapter, job
// tracker and HTTP client together, without any real slide/API requests.
test("worker restart and Retry reconnect without downloading or uploading the slide again", async () => {
  const records = {};
  let messageHandler, alarmHandler, downloadCount = 0, status = "running";
  const calls = [];
  const event = (capture = () => {}) => ({ addListener: capture });
  const local = {
    get: async (key) => key === null ? structuredClone(records) : { [key]: structuredClone(records[key]) },
    set: async (value) => Object.assign(records, structuredClone(value)),
  };
  globalThis.chrome = {
    storage: { local, sync: { get: async (defaults) => ({ ...defaults, backendUrl: "https://example.test" }) } },
    action: { setBadgeText: () => {}, setBadgeBackgroundColor: () => {} },
    tabs: { sendMessage: async () => {} },
    runtime: {
      onMessage: event((fn) => { messageHandler = fn; }),
      onInstalled: event(), onStartup: event(), sendMessage: async () => {},
    },
    alarms: { get: async () => ({}), create: async () => {}, onAlarm: event((fn) => { alarmHandler = fn; }) },
    downloads: {
      download: async () => { downloadCount++; return 77; },
      search: async () => [{ state: "complete" }],
    },
  };
  mock.method(globalThis, "fetch", async (url, options = {}) => {
    calls.push([url, options.method || "GET"]);
    if (url.endsWith("/health")) return Response.json({ status: "ok", ollama: true });
    if (url.includes("pesuacademy.com")) return new Response("%PDF-source", { headers: { "content-type": "application/pdf" } });
    if (url.endsWith("/jobs")) return Response.json({ job_id: "saved-job" }, { status: 202 });
    if (url.endsWith("/pdf")) return new Response("%PDF-summary");
    return Response.json({ status, progress: "Reading diagrams", cached: false, summary_id: "summary" });
  });
  const send = (message) => new Promise((resolve) => {
    assert.equal(messageHandler(message, { tab: { id: 1 } }, resolve), true);
  });
  const flush = async () => { for (let i = 0; i < 15; i++) await new Promise(setImmediate); };
  try {
    await import("../extension/background.js?worker=1");
    const request = {
      type: "summarize-url", title: "Lecture", requestId: "click-1",
      url: "https://pesuacademy.com/Academy/a/referenceMeterials/downloadslidecoursedoc/12345678-1234-1234-1234-123456789abc",
    };
    const first = await send(request);
    assert.equal(first.ok, true);
    assert.equal(first.task.jobId, "saved-job");
    await flush();
    // A new module instance represents Chrome replacing its worker, while
    // chrome.storage.local survives it.
    await import("../extension/background.js?worker=2");
    const second = await send({ ...request, requestId: "click-2" });
    assert.equal(second.task.jobId, "saved-job");
    await flush();
    assert.equal(calls.filter(([, method]) => method === "POST").length, 1);
    assert.equal(calls.filter(([url]) => url.includes("pesuacademy.com")).length, 1);
    status = "done";
    alarmHandler({ name: "check-summaries" });
    await flush();
    alarmHandler({ name: "check-summaries" });
    await flush();
    const completed = await send({ type: "get-task", key: first.task.key });
    assert.equal(completed.task.state, "done");
    assert.equal(downloadCount, 1);
  } finally {
    mock.restoreAll();
    delete globalThis.chrome;
  }
});
