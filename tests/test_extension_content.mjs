import assert from 'node:assert/strict';
import { test } from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

function harness(sendMessage) {
  const timers = new Map(); let next = 0;
  const context = vm.createContext({
    chrome: { runtime: { sendMessage, onMessage: { addListener() {} } } },
    document: { body: {}, querySelectorAll: () => [] },
    MutationObserver: class { observe() {} },
    setTimeout: (fn) => { timers.set(++next, fn); return next; },
    clearTimeout: (id) => timers.delete(id),
  });
  vm.runInContext(readFileSync(new URL('../extension/content.js', import.meta.url), 'utf8'), context);
  return { context, timers };
}

test('unresponsive worker produces a recoverable message without repeating submission', async () => {
  let calls = 0;
  const { context, timers } = harness(() => { calls++; return new Promise(() => {}); });
  const pending = vm.runInContext('sendToWorker({type:"summarize-url"}, 1)', context);
  const rejected = assert.rejects(pending, /Reload Slide Summarizer.*submitted job may still be running/);
  await Promise.resolve();
  [...timers.values()][0]();
  await rejected;
  assert.equal(calls, 1);
});

test('normal worker response clears watchdog and is returned unchanged', async () => {
  const { context, timers } = harness(async () => ({ ok: true, task: { jobId: 'saved' } }));
  const result = await vm.runInContext('sendToWorker({type:"get-task"})', context);
  assert.equal(result.task.jobId, 'saved');
  assert.equal(timers.size, 0);
});
