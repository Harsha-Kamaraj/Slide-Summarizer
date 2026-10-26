// Retry only reads of an existing job. Repeating POST /jobs could create
// duplicate summaries when the upload succeeded but its response was lost.
const RETRY_DELAYS_MS = [2000, 4000, 8000, 10000];
const READ_TIMEOUT_MS = 20000;

export async function fetchOk(url, options) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (cause) {
    const error = new Error(
      `Can't reach the summarizer at ${new URL(url).origin}. Check your connection.`,
      { cause },
    );
    error.retryable = true;
    throw error;
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(
      typeof body.detail === "string"
        ? body.detail
        : `Summarizer returned HTTP ${response.status} for ${new URL(url).pathname}.`,
    );
    error.status = response.status;
    error.retryable = [502, 503, 504].includes(response.status);
    throw error;
  }
  return response;
}

export async function readJob(url, format, onProgress) {
  for (let attempt = 0; ; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), READ_TIMEOUT_MS);
    let failure;
    try {
      const response = await fetchOk(url, { signal: controller.signal });
      // Keep the timeout and retry active until the entire body arrives.
      return await (format === "blob" ? response.blob() : response.json());
    } catch (error) {
      const retryable = error.retryable === true || controller.signal.aborted ||
        error instanceof TypeError;
      console.warn(`Job read failed (${new URL(url).pathname}, attempt ${attempt + 1})`, error);
      if (!retryable) throw error;
      if (attempt === RETRY_DELAYS_MS.length) {
        throw new Error(
          "Connection to the summary was lost after several attempts. " +
          "The server may still be processing it. Check your connection and try again.",
          { cause: error },
        );
      }
      failure = error;
    } finally {
      clearTimeout(timer);
    }
    if (failure) {
      await onProgress("Connection interrupted. Reconnecting to the same summary…");
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS_MS[attempt]));
    }
  }
}
