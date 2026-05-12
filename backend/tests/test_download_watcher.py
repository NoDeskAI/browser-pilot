import asyncio
import json
import shlex

from app import download_watcher
from app import file_service


def test_list_downloads_skips_incomplete_crdownload(monkeypatch):
    async def fake_run(_cmd, timeout=5):
        payload = [
            {
                "path": "/home/seluser/Downloads/a.txt.crdownload",
                "name": "a.txt.crdownload",
                "size": 10,
                "mtime": 1.0,
            },
            {
                "path": "/home/seluser/Downloads/a.txt",
                "name": "a.txt",
                "size": 10,
                "mtime": 2.0,
            },
        ]
        return json.dumps(payload), "", 0

    monkeypatch.setattr(download_watcher, "_run", fake_run)

    result = asyncio.run(download_watcher._list_downloads("session-1"))

    assert result == [
        {
            "path": "/home/seluser/Downloads/a.txt",
            "name": "a.txt",
            "size": 10,
            "mtime": 2.0,
        }
    ]


def test_download_watcher_default_does_not_start_legacy_task(monkeypatch):
    monkeypatch.setattr(download_watcher, "_watchers", {})
    monkeypatch.setattr("app.config.BP_LEGACY_DOCKER_DOWNLOAD_WATCHER", False)

    download_watcher.start_download_watcher("session-1")

    assert download_watcher._watchers == {}


def test_download_watcher_legacy_flag_starts_task(monkeypatch):
    created = []

    class FakeTask:
        def done(self):
            return False

    def fake_create_task(coro):
        coro.close()
        created.append(coro)
        return FakeTask()

    monkeypatch.setattr(download_watcher, "_watchers", {})
    monkeypatch.setattr("app.config.BP_LEGACY_DOCKER_DOWNLOAD_WATCHER", True)
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    download_watcher.start_download_watcher("session-1")

    assert "session-1" in download_watcher._watchers
    assert len(created) == 2


def test_upload_download_uses_file_service_with_source_metadata(monkeypatch):
    saved = {}

    async def fake_run(cmd, timeout=30):
        dest = shlex.split(cmd)[-1]
        with open(dest, "wb") as fh:
            fh.write(b"downloaded")
        return "", "", 0

    async def fake_save_file(**kwargs):
        kwargs["data"] = kwargs["path"].read_bytes()
        saved.update(kwargs)
        return {"id": "file-1"}

    monkeypatch.setattr(download_watcher, "_run", fake_run)
    monkeypatch.setattr(file_service, "save_file", fake_save_file)

    result = asyncio.run(
        download_watcher._upload_download(
            "session-1",
            {
                "path": "/home/seluser/Downloads/report.txt",
                "name": "report.txt",
                "size": 10,
                "mtime": 123.0,
            },
        )
    )

    assert result == {"id": "file-1"}
    assert saved["session_id"] == "session-1"
    assert saved["source"] == "browser_download"
    assert saved["filename"] == "report.txt"
    assert saved["data"] == b"downloaded"
    assert saved["source_path"] == "/home/seluser/Downloads/report.txt"
    assert saved["source_mtime"] == 123.0
