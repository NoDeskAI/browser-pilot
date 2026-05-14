import importlib.util
import sys
from pathlib import Path


AGENT_PATH = Path(__file__).resolve().parents[2] / "services" / "selenium-chrome" / "file-capture-agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("file_capture_agent", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_agent_uploads_only_completed_download_progress(tmp_path):
    agent_mod = _load_agent_module()
    download = tmp_path / "report.txt"
    download.write_text("hello")
    uploads = []
    heartbeats = []

    def upload_func(*args):
        uploads.append(args)
        return {"ok": True}

    def heartbeat_func(*args):
        heartbeats.append(args)

    agent = agent_mod.FileCaptureAgent(
        backend_url="http://backend:8000",
        session_id="session-1",
        token="bpr_token",
        download_dir=tmp_path,
        upload_func=upload_func,
        heartbeat_func=heartbeat_func,
    )

    agent.handle_message({
        "method": "Browser.downloadWillBegin",
        "params": {"guid": "guid-1", "suggestedFilename": "report.txt", "url": "https://example.com/report.txt"},
    })
    assert heartbeats[-1][2]["downloads"][0]["id"] == "guid-1"
    assert heartbeats[-1][2]["downloads"][0]["name"] == "report.txt"
    assert heartbeats[-1][2]["downloads"][0]["sourceUrl"] == "https://example.com/report.txt"

    agent.handle_message({
        "method": "Browser.downloadProgress",
        "params": {"guid": "guid-1", "state": "inProgress", "receivedBytes": 1, "totalBytes": 5},
    })
    assert heartbeats[-1][2]["downloads"][0]["receivedBytes"] == 1
    assert heartbeats[-1][2]["downloads"][0]["totalBytes"] == 5
    assert heartbeats[-1][2]["downloads"][0]["percent"] == 20.0

    agent.handle_message({
        "method": "Browser.downloadProgress",
        "params": {"guid": "guid-2", "state": "canceled"},
    })
    assert uploads == []

    agent.handle_message({
        "method": "Browser.downloadProgress",
        "params": {"guid": "guid-1", "state": "completed", "filePath": str(download)},
    })

    assert len(uploads) == 1
    url, token, fields, path, filename, content_type = uploads[0]
    assert url == "http://backend:8000/api/sessions/session-1/files/ingest"
    assert token == "bpr_token"
    assert fields["source"] == "browser_download"
    assert fields["sourceId"] == "guid-1"
    assert fields["originalName"] == "report.txt"
    assert fields["sizeBytes"] == "5"
    assert path == download
    assert filename == "report.txt"
    assert content_type == "text/plain"
    assert heartbeats[-1][2]["status"] == "running"
    assert heartbeats[-1][2]["downloads"] == []


def test_agent_directory_fallback_filters_crdownload_and_waits_for_stability(tmp_path):
    agent_mod = _load_agent_module()
    uploads = []
    fallback = agent_mod.DirectoryFallback(tmp_path, lambda path, source_id: uploads.append((path.name, source_id)))
    complete = tmp_path / "done.txt"
    partial = tmp_path / "done.txt.crdownload"
    complete.write_text("done")
    partial.write_text("partial")

    fallback.maybe_poll(now=1.0)
    fallback.maybe_poll(now=2.1)

    assert uploads == [("done.txt", None)]


def test_agent_does_not_reference_s3_credentials():
    source = AGENT_PATH.read_text()

    assert "AWS_" not in source
    assert "S3_" not in source
    assert "MINIO_" not in source
