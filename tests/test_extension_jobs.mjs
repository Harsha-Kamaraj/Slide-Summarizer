import assert from "node:assert/strict";
import { test, mock, afterEach } from "node:test";
import { JobTracker, taskKey } from "../extension/job-tracker.js";

const settings = { backendUrl: "https://example.test", mode: "detailed", examQuestions: false, flashcards: false };
const key = await taskKey("slide-1", settings);
const seed = { key, backendUrl: settings.backendUrl, filename: "lecture.pdf" };

function setup() {
  const records = new Map();
  const calls = { uploads: 0, reads: [], downloads: 0 };
  const storage = {
    get: async (key) => structuredClone(records.get(key)),
    set: async (key, task) => records.set(key, structuredClone(task)),
    all: async () => structuredClone([...records.values()]),
  };
  const options = {
    storage,
    readJob: async (url, format) => {
      calls.reads.push(url);
      return format === "blob" ? new Blob(["%PDF-test"]) : { status: "running", progress: "Reading slides" };
    },
    download: async () => { calls.downloads++; return 123; },
    getDownload: async () => ({ state: "complete" }),
  };
  const submit = async () => { calls.uploads++; return "original-job"; };
  return { records, calls, options, submit, tracker: new JobTracker(options) };
}

afterEach(() => mock.restoreAll());

test("Retry after connection loss resumes the saved job after a worker restart", async () => {
  mock.method(console, "warn", () => {});
  const { tracker, options, calls, submit } = setup();
  await tracker.start(seed, submit);
  tracker.readJob = async () => { throw new TypeError("offline"); };
  assert.equal((await tracker.check(key, true)).state, "paused");
  const restarted = new JobTracker(options);
  await restarted.start(seed, submit);
  assert.equal((await restarted.check(key, true)).state, "active");
  assert.equal(calls.uploads, 1);
  assert.equal(calls.reads.at(-1), "https://example.test/jobs/original-job");
});

test("concurrent clicks submit only one upload", async () => {
  const { tracker, submit, calls } = setup();
  const [a, b] = await Promise.all([tracker.start(seed, submit), tracker.start(seed, submit)]);
  assert.equal(a.jobId, b.jobId);
  assert.equal(calls.uploads, 1);
});

test("an alarm resumes an active saved job after worker recreation", async () => {
  const { tracker, options, submit, calls } = setup();
  await tracker.start(seed, submit);
  await new JobTracker(options).checkActive();
  assert.equal(calls.reads.length, 1);
  assert.equal(calls.uploads, 1);
});

test("completed summary downloads without repeating AI work and survives download handoff", async () => {
  const { tracker, options, submit, calls } = setup();
  options.readJob = tracker.readJob = async (url, format) => {
    calls.reads.push(url);
    return format === "blob" ? new Blob(["%PDF-test"]) : { status: "done", cached: true, summary_id: "summary" };
  };
  await tracker.start(seed, submit);
  assert.equal((await tracker.check(key, true)).state, "downloading");
  const restarted = new JobTracker(options);
  const done = await restarted.check(key, true);
  assert.equal(done.state, "done");
  assert.equal(done.cached, true);
  assert.equal(calls.downloads, 1);
  assert.equal(calls.uploads, 1);
});

test("server job expiry is reported without silently uploading again", async () => {
  mock.method(console, "warn", () => {});
  const { tracker, submit, calls } = setup();
  await tracker.start(seed, submit);
  tracker.readJob = async () => { throw Object.assign(new Error("Not found"), { status: 404 }); };
  const expired = await tracker.check(key, true);
  assert.equal(expired.state, "expired");
  assert.match(expired.error, /restarted|expired/);
  assert.equal(calls.uploads, 1);
  // A subsequent explicit click can submit a fresh job.
  await tracker.start(seed, submit);
  assert.equal(calls.uploads, 2);
});

test("a processing error is shown unchanged, not mislabeled as a network failure", async () => {
  const { tracker, submit } = setup();
  await tracker.start(seed, submit);
  tracker.readJob = async () => ({ status: "error", error: "AI request too large" });
  const task = await tracker.check(key, true);
  assert.equal(task.state, "failed");
  assert.equal(task.error, "AI request too large");
});

test("source download failure never submits a job and retains its specific error", async () => {
  const { tracker, calls } = setup();
  const task = await tracker.start(seed, async () => { throw new Error("PESU download failed"); });
  assert.equal(task.state, "failed");
  assert.equal(task.jobId, undefined);
  assert.equal(task.error, "PESU download failed");
  assert.equal(calls.reads.length, 0);
});

test("an interrupted upload is not automatically repeated after restart", async () => {
  const { records, tracker, calls } = setup();
  records.set(key, { ...seed, state: "starting", progress: "Uploading" });
  await tracker.checkActive();
  const task = await tracker.get(key);
  assert.equal(task.state, "failed");
  assert.match(task.error, /may have received/);
  assert.equal(calls.uploads, 0);
});

test("mode, extras, source and backend each separate saved jobs", async () => {
  const keys = await Promise.all([
    taskKey("slide-1", settings),
    taskKey("slide-2", settings),
    taskKey("slide-1", { ...settings, mode: "short" }),
    taskKey("slide-1", { ...settings, flashcards: true }),
    taskKey("slide-1", { ...settings, backendUrl: "https://other.test" }),
  ]);
  assert.equal(new Set(keys).size, keys.length);
  assert.equal(await taskKey("slide-1", { ...settings, backendUrl: settings.backendUrl + "/" }), key);
});

test("explicitly summarizing a completed deck submits once for current backend layout", async () => {
  const { tracker, records, calls, submit } = setup();
  records.set(key, { ...seed, jobId: "old-job", state: "done", downloadId: 77 });
  const fresh = { ...seed, refreshCompleted: true };
  await Promise.all([tracker.start(fresh, submit), tracker.start(fresh, submit)]);
  assert.equal(calls.uploads, 1);
  assert.equal((await tracker.get(key)).jobId, "original-job");
  assert.equal((await tracker.get(key)).downloadId, undefined);
  // Another click while active still reconnects; never starts duplicate AI work.
  await tracker.start(fresh, submit);
  assert.equal(calls.uploads, 1);
});

test("explicit resume of a finished download retains its job", async () => {
  const { tracker, records, calls, submit } = setup();
  records.set(key, { ...seed, jobId: "old-job", state: "done", downloadId: 77 });
  const task = await tracker.start(seed, submit);
  assert.equal(task.jobId, "old-job");
  assert.equal(calls.uploads, 0);
  assert.equal(task.downloadId, undefined);
});

test("cancel targets one job, survives worker restart, and allows explicit retry", async () => {
  const { tracker, options, submit, calls } = setup();
  const urls = [];
  tracker.cancelJob = async (url) => { urls.push(url); return {status: 'cancelling', progress: 'Stopping…'}; };
  await tracker.start(seed, submit);
  assert.equal((await tracker.cancel(key)).state, 'cancelling');
  assert.deepEqual(urls, ['https://example.test/jobs/original-job/cancel']);
  assert.equal((await tracker.start(seed, submit)).state, 'cancelling');
  assert.equal(calls.uploads, 1);
  options.readJob = async () => ({status: 'cancelled', progress: 'Cancelled'});
  const restarted = new JobTracker(options);
  assert.equal((await restarted.check(key, true)).state, 'cancelled');
  await restarted.checkActive();
  assert.equal(calls.downloads, 0);
  await restarted.start(seed, submit);
  assert.equal(calls.uploads, 2);
});

test("stale status read cannot undo an acknowledged cancellation or download a PDF", async () => {
  const { tracker, submit, calls } = setup();
  await tracker.start(seed, submit);
  let release;
  const reading = new Promise(resolve => { release = resolve; });
  tracker.readJob = async () => reading;
  tracker.cancelJob = async () => {
    release({status: 'done'});
    return {status: 'cancelled', progress: 'Cancelled'};
  };
  const check = tracker.check(key, true);
  await new Promise(resolve => setImmediate(resolve));
  await Promise.all([check, tracker.cancel(key)]);
  assert.equal((await tracker.get(key)).state, 'cancelled');
  assert.equal(calls.downloads, 0);
});

test("failed cancel request never falsely reports cancellation", async () => {
  const { tracker, submit } = setup();
  await tracker.start(seed, submit);
  tracker.cancelJob = async () => { throw new Error('offline'); };
  await assert.rejects(tracker.cancel(key), /offline/);
  assert.equal((await tracker.get(key)).state, 'active');
});
