import assert from "node:assert/strict";
import { beforeEach, afterEach, mock, test } from "node:test";
import { fetchOk, readJob } from "../extension/backend-api.js";

const url = "https://example.test/jobs/existing-job";
const realSetTimeout = globalThis.setTimeout;

beforeEach(() => {
  // Exercise real abort signals and timers without waiting minutes.
  mock.method(globalThis, "setTimeout", (fn, delay) => realSetTimeout(fn, Math.min(delay, 10)));
  mock.method(console, "warn", () => {});
});
afterEach(() => mock.restoreAll());

test("a dropped progress request reconnects to the same job", async () => {
  const calls = [];
  mock.method(globalThis, "fetch", async (target, options) => {
    calls.push([target, options.method || "GET"]);
    if (calls.length === 1) throw new TypeError("Failed to fetch");
    return Response.json({ status: "running", progress: "Reading slides" });
  });
  const updates = [];
  assert.equal((await readJob(url, "json", (text) => updates.push(text))).status, "running");
  assert.deepEqual(calls, [[url, "GET"], [url, "GET"]]);
  assert.match(updates[0], /Reconnecting/);
});

test("temporary gateway failures recover", async () => {
  let calls = 0;
  mock.method(globalThis, "fetch", async () => {
    calls++;
    return calls === 1 ? new Response("Unavailable", { status: 503 }) : Response.json({ status: "done" });
  });
  assert.equal((await readJob(url, "json", () => {})).status, "done");
  assert.equal(calls, 2);
});

test("PDF body connection loss retries the download", async () => {
  let calls = 0;
  mock.method(globalThis, "fetch", async () => {
    calls++;
    if (calls === 1) return { ok: true, blob: async () => { throw new TypeError("Body interrupted"); } };
    return new Response("%PDF-test");
  });
  const pdf = await readJob(`${url}/pdf`, "blob", () => {});
  assert.equal(await pdf.text(), "%PDF-test");
  assert.equal(calls, 2);
});

test("an unresponsive read is aborted and retried", async () => {
  let calls = 0;
  let firstSignal;
  mock.method(globalThis, "fetch", async (_, { signal }) => {
    calls++;
    if (calls > 1) return Response.json({ status: "done" });
    firstSignal = signal;
    return new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    });
  });
  assert.equal((await readJob(url, "json", () => {})).status, "done");
  assert.equal(firstSignal.aborted, true);
  assert.equal(calls, 2);
});

test("an expired job stops immediately with the server's explanation", async () => {
  const fetch = mock.method(globalThis, "fetch", async () => Response.json(
    { detail: "Unknown or expired job. Please summarize again." }, { status: 404 },
  ));
  await assert.rejects(readJob(url, "json", () => {}), /Unknown or expired job/);
  assert.equal(fetch.mock.callCount(), 1);
});

test("persistent connection failures stop after five requests", async () => {
  const fetch = mock.method(globalThis, "fetch", async () => { throw new TypeError("Offline"); });
  await assert.rejects(readJob(url, "json", () => {}), /server may still be processing/);
  assert.equal(fetch.mock.callCount(), 5);
});

test("a failed upload is never automatically submitted again", async () => {
  const fetch = mock.method(globalThis, "fetch", async () => { throw new TypeError("Response lost"); });
  await assert.rejects(fetchOk("https://example.test/jobs", { method: "POST", body: "upload" }), /Can't reach/);
  assert.equal(fetch.mock.callCount(), 1);
});
