"""The summary cache's optional remote layer (Hugging Face dataset), with the
network replaced by fakes."""

from pathlib import Path

import pytest

from backend import cache, config


@pytest.fixture
def remote(monkeypatch, tmp_path):
    """A fake dataset: files live in a dict; uploads happen synchronously."""
    store: dict[str, str] = {}
    monkeypatch.setattr(config, "CACHE_HF_REPO", "user/cache")

    def fake_download(repo_id, filename, **kwargs):
        if filename not in store:
            raise cache.EntryNotFoundError("missing")
        path = tmp_path / "dl" / filename
        path.parent.mkdir(exist_ok=True)
        path.write_text(store[filename])
        return str(path)

    class FakeApi:
        def __init__(self, token=None):
            pass

        def upload_file(self, path_or_fileobj, path_in_repo, **kwargs):
            store[path_in_repo] = path_or_fileobj.decode()

    class InlineThread:  # run "background" uploads immediately
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(cache, "hf_hub_download", fake_download)
    monkeypatch.setattr(cache, "HfApi", FakeApi)
    monkeypatch.setattr(cache.threading, "Thread", InlineThread)
    return store


def test_put_uploads_and_get_reads_back_after_disk_is_wiped(remote):
    cache.put("abc", "# Summary")
    assert remote == {"abc.md": "# Summary"}

    # Simulate a restart on a host whose disk is wiped.
    Path(config.CACHE_DIR, "abc.md").unlink()
    assert cache.get("abc") == "# Summary"
    assert Path(config.CACHE_DIR, "abc.md").exists()  # copied back to disk


def test_remote_miss_and_remote_failure_are_both_just_misses(remote, monkeypatch):
    assert cache.get("nope") is None

    def broken(*args, **kwargs):
        raise ConnectionError("hub down")

    monkeypatch.setattr(cache, "hf_hub_download", broken)
    assert cache.get("nope") is None  # logged, not raised


def test_no_remote_configured_uses_disk_only(monkeypatch):
    monkeypatch.setattr(cache, "hf_hub_download", lambda *a, **k: pytest.fail("no network"))
    cache.put("k", "text")
    assert cache.get("k") == "text"
    assert cache.get("missing") is None
