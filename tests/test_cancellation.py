"""Cancel queued/running jobs without cancelling neighbours or deleting checkpoints."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from backend import cache, execution, jobs, main, service

client = TestClient(main.app)


def wait(job, status):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        if jobs.get(job.id).status == status:
            return jobs.get(job.id)
        time.sleep(.01)
    raise AssertionError(f"Expected {status}, got {jobs.get(job.id).status}")


@pytest.fixture
def queue(monkeypatch):
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(jobs, '_executor', pool)
    monkeypatch.setattr(jobs, '_jobs', {})
    monkeypatch.setattr(jobs, '_controls', {})
    yield
    for event, _, _ in list(jobs._controls.values()):
        event.set()
    pool.shutdown(wait=True, cancel_futures=True)


def submit(name):
    directory = jobs.new_upload_dir()
    path = directory / name
    path.write_bytes(b'fake deck')
    return jobs.submit(directory, path, name, 'short', []), path


def test_cancel_queued_and_running_preserves_other_job_and_cache(queue, monkeypatch):
    started = threading.Event()
    visited = []
    checkpoint = cache.object_key('test', 'completed-section')

    def pipeline(path, *args):
        visited.append(path.name)
        if path.name == 'first.pdf':
            cache.put(checkpoint, 'already generated notes')
            started.set()
            execution.sleep(30)  # cancellation must wake the wait immediately
        return '# Done', False

    monkeypatch.setattr(service, 'run', pipeline)
    first, first_path = submit('first.pdf')
    assert started.wait(2)
    second, second_path = submit('second.pdf')
    third, third_path = submit('third.pdf')
    response = client.post(f'/jobs/{second.id}/cancel')
    assert response.json()['status'] == 'cancelled'
    assert not second_path.exists()
    assert client.post(f'/jobs/{second.id}/cancel').json()['status'] == 'cancelled'
    assert client.post(f'/jobs/{first.id}/cancel').status_code == 200
    wait(first, 'cancelled')
    wait(third, 'done')
    assert visited == ['first.pdf', 'third.pdf']
    assert not first_path.exists() and not third_path.exists()
    assert cache.get(checkpoint) == 'already generated notes'
    assert client.get(f'/jobs/{first.id}/pdf').status_code == 409
    assert client.post(f'/jobs/{third.id}/cancel').json()['status'] == 'done'
    assert client.post('/jobs/not-a-job/cancel').status_code == 404
    # Finished cancellation is recoverable after a process restart.
    jobs._save(jobs.get(first.id))
    jobs._jobs.pop(first.id)
    assert jobs.get(first.id).status == 'cancelled'


def test_inflight_operation_finishes_but_cancel_wins_over_result(queue, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def pipeline(*args):
        started.set()
        assert release.wait(2)
        return '# Completed while cancelling', False
    monkeypatch.setattr(service, 'run', pipeline)
    job, path = submit('inflight.pdf')
    assert started.wait(2)
    try:
        assert client.post(f'/jobs/{job.id}/cancel').json()['status'] == 'cancelling'
    finally:
        release.set()
    result = wait(job, 'cancelled')
    assert result.markdown == '' and not path.exists()


def test_cancellation_context_propagates_to_parallel_sections():
    event = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool, execution.cancellable(event):
        event.set()
        with pytest.raises(execution.Cancelled):
            execution.parallel_map(pool, lambda _: execution.remaining(), [1, 2])
    # Scope is reset for the next unrelated request.
    assert execution.remaining() == float('inf')


def test_cancel_during_ai_response_keeps_reply_but_stops_next_request(monkeypatch):
    import httpx
    from backend import llm
    event = threading.Event()
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        event.set()  # user cancels while the remote operation is finishing
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Saved section'}}]})
    monkeypatch.setattr(llm.httpx, 'post', post)
    messages = [{'role': 'user', 'content': 'First section'}]
    with pytest.raises(execution.Cancelled), execution.cancellable(event):
        assert llm.chat(messages) == 'Saved section'
        llm.chat([{'role': 'user', 'content': 'Next section'}])
    assert len(calls) == 1
    assert llm.chat(messages) == 'Saved section'
    assert len(calls) == 1  # an explicit retry can reuse the saved section
