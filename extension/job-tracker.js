// The durable record lives in chrome.storage.local, not in the service
// worker's memory. Only coalescing locks are kept in memory.
export const TASK_PREFIX = "summary-task:";
export const ACTIVE_STATES = ["starting", "active", "reconnecting", "downloading"];

export async function taskKey(source, settings) {
  const identity = JSON.stringify([
    source, settings.backendUrl.replace(/\/+$/, ""), settings.mode,
    Boolean(settings.examQuestions), Boolean(settings.flashcards),
  ]);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(identity));
  return TASK_PREFIX + Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export class JobTracker {
  constructor({ storage, readJob, download, getDownload, onChange = () => {} }) {
    Object.assign(this, { storage, readJob, download, getDownload, onChange });
    this.starts = new Map();
    this.checks = new Map();
  }

  async save(task) {
    task.updatedAt = Date.now();
    await this.storage.set(task.key, task);
    this.onChange(task);
    return task;
  }

  async get(key) {
    if (!key?.startsWith(TASK_PREFIX)) throw new Error("Invalid summary reference.");
    const task = await this.storage.get(key);
    if (task?.state === "starting" && !this.starts.has(key)) {
      // The worker stopped before we could save the upload response. Never
      // silently resubmit: the server might already be processing that file.
      task.state = "failed";
      task.error = "The upload was interrupted before its job number was saved. " +
        "The server may have received it. Click Retry to submit again.";
      await this.save(task);
    }
    return task;
  }

  start(seed, submit) {
    if (this.starts.has(seed.key)) return this.starts.get(seed.key);
    const promise = this.startOnce(seed, submit).finally(() => this.starts.delete(seed.key));
    this.starts.set(seed.key, promise);
    return promise;
  }

  async startOnce(seed, submit) {
    // Wait for an already-running check to settle before changing its state.
    if (this.checks.has(seed.key)) await this.checks.get(seed.key);
    const previous = await this.storage.get(seed.key);
    if (previous?.jobId && !["failed", "expired"].includes(previous.state)) {
      // Retry and repeated clicks resume the known job, with no PDF transfer.
      if (previous.state === "done") delete previous.downloadId; // explicit re-download
      previous.state = "active";
      previous.progress = "Reconnecting to your saved summary…";
      previous.error = "";
      previous.lastChecked = 0;
      return this.save(previous);
    }

    const task = { ...seed, state: "starting", progress: "Preparing summary…", error: "" };
    await this.save(task);
    try {
      const jobId = await submit(async (progress) => {
        task.progress = progress;
        await this.save(task);
      });
      if (typeof jobId !== "string" || !/^[a-zA-Z0-9_-]+$/.test(jobId)) {
        throw new Error("The upload response did not contain a valid job number. " +
          "The server may have received the file; check its logs before submitting again.");
      }
      task.jobId = jobId;
      task.state = "active";
      task.progress = "Summary queued. Checking progress…";
    } catch (error) {
      task.state = "failed";
      task.error = error.message;
    }
    return this.save(task);
  }

  check(key, force = false) {
    if (this.checks.has(key)) return this.checks.get(key);
    const promise = this.checkOnce(key, force).finally(() => this.checks.delete(key));
    this.checks.set(key, promise);
    return promise;
  }

  async checkOnce(key, force) {
    const task = await this.get(key);
    if (!task?.jobId || !ACTIVE_STATES.includes(task.state)) return task;
    if (!force && Date.now() - (task.lastChecked || 0) < 2500) return task;
    task.lastChecked = Date.now();
    await this.save(task);
    const progress = async (text) => {
      task.state = "reconnecting";
      task.progress = text;
      await this.save(task);
    };
    try {
      // A browser download can outlive this worker. Check it before starting
      // another, and only report success when Chrome confirms completion.
      if (task.downloadId !== undefined) {
        const item = await this.getDownload(task.downloadId);
        if (item?.state === "complete") {
          task.state = "done";
          task.progress = task.cached ? "Saved · from cache" : "Saved to Downloads";
          return await this.save(task);
        }
        if (item?.state === "in_progress") return task;
        delete task.downloadId;
        throw new Error("The PDF download was interrupted. Retry will download the saved summary again.");
      }

      const url = `${task.backendUrl}/jobs/${task.jobId}`;
      const status = await this.readJob(url, "json", progress);
      task.error = "";
      if (["queued", "running"].includes(status.status)) {
        task.state = "active";
        task.progress = status.progress || "Processing summary…";
      } else if (status.status === "error") {
        task.state = "failed";
        task.error = status.error || "The server could not finish this summary.";
      } else if (status.status === "done") {
        task.cached = status.cached;
        task.summaryId = status.summary_id;
        task.state = "downloading";
        task.progress = "Downloading summary…";
        await this.save(task);
        const blob = await this.readJob(`${url}/pdf`, "blob", progress);
        task.downloadId = await this.download(blob, task.filename);
        task.state = "downloading";
        task.progress = "Saving PDF to Downloads…";
      } else {
        throw new Error("The server returned an unexpected job status. Retry will check the same job.");
      }
    } catch (error) {
      console.warn(`Summary ${task.jobId}:`, error);
      if (error.status === 404) {
        task.state = "expired";
        task.error = "The server no longer has this job; it may have restarted or the job expired. " +
          "Click Retry to start again. A completed cached summary can still be reused.";
      } else {
        task.state = "paused";
        task.error = "Couldn't finish checking or downloading this summary. " +
          "Your job number is saved. Click Retry to reconnect without uploading again. " + error.message;
      }
    }
    return this.save(task);
  }

  async checkActive() {
    const tasks = await this.storage.all();
    await Promise.allSettled(tasks.filter((task) => ACTIVE_STATES.includes(task.state))
      .map((task) => this.check(task.key, true)));
  }
}
