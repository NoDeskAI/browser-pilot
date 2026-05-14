#!/usr/bin/env python3
"""Capture Chrome downloads inside the browser runtime and ingest them upstream."""

from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


CDP_VERSION_URL = "http://localhost:9222/json/version"
DEFAULT_DOWNLOAD_DIR = "/home/seluser/Downloads"
HEALTH_FILE = Path("/tmp/file-capture-health.json")
RETRY_INTERVAL_SECONDS = 1.0
MAX_DISCOVERY_ATTEMPTS = 120
HEARTBEAT_INTERVAL_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 1.0
STABLE_POLLS = 2
UPLOAD_CHUNK_SIZE = 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_health(payload: dict[str, Any]) -> None:
    payload.setdefault("updatedAt", _now())
    try:
        tmp = HEALTH_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(HEALTH_FILE)
    except OSError:
        pass


def _safe_filename(name: str, default: str = "download") -> str:
    value = Path(str(name or "")).name.strip().strip(" .")
    return value or default


def _content_type(path: Path, fallback: str = "application/octet-stream") -> str:
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or fallback


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(UPLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discover_browser_ws() -> str | None:
    for attempt in range(1, MAX_DISCOVERY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(CDP_VERSION_URL, timeout=2) as resp:
                data = json.loads(resp.read())
            ws_url = data.get("webSocketDebuggerUrl")
            if ws_url:
                return str(ws_url)
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if attempt < MAX_DISCOVERY_ATTEMPTS:
            time.sleep(RETRY_INTERVAL_SECONDS)
    return None


def _multipart_field(name: str, value: str, boundary: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _multipart_file_header(name: str, filename: str, content_type: str, boundary: str) -> bytes:
    safe_name = filename.replace('"', "_")
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{safe_name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")


def _upload_multipart(
    url: str,
    token: str,
    fields: dict[str, str],
    file_path: Path,
    filename: str,
    content_type: str,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"Unsupported ingest URL: {url}")

    boundary = f"----browser-pilot-{hashlib.sha256(os.urandom(16)).hexdigest()}"
    preamble = bytearray()
    for key, value in fields.items():
        preamble.extend(_multipart_field(key, value, boundary))
    preamble.extend(_multipart_file_header("file", filename, content_type, boundary))
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    content_length = len(preamble) + file_path.stat().st_size + len(footer)

    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    port = parsed.port
    conn = connection_cls(parsed.hostname, port, timeout=60)
    try:
        conn.putrequest("POST", target)
        conn.putheader("Authorization", f"Bearer {token}")
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(content_length))
        conn.endheaders()
        conn.send(bytes(preamble))
        with file_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(UPLOAD_CHUNK_SIZE), b""):
                conn.send(chunk)
        conn.send(footer)

        response = conn.getresponse()
        body = response.read()
        if response.status >= 400:
            preview = body[:300].decode("utf-8", errors="replace")
            raise RuntimeError(f"Ingest failed with HTTP {response.status}: {preview}")
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))
    finally:
        conn.close()


def _post_json(url: str, token: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10):
        pass


def _build_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


@dataclass
class DownloadRecord:
    guid: str
    suggested_filename: str = ""
    url: str = ""
    file_path: str = ""
    received_bytes: int | None = None
    total_bytes: int | None = None
    started_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    uploaded: bool = False


@dataclass
class DirectoryFallback:
    download_dir: Path
    uploader: Callable[[Path, str | None], None]
    processed: set[tuple[str, float, int]] = field(default_factory=set)
    stable: dict[str, tuple[int, float, int]] = field(default_factory=dict)
    last_poll_at: float = 0.0

    def prime(self) -> None:
        self.processed.update(_entry_signature(item) for item in _scan_downloads(self.download_dir))

    def mark_processed(self, path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        self.processed.add((str(path), float(stat.st_mtime), int(stat.st_size)))
        self.stable.pop(str(path), None)

    def maybe_poll(self, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        if now - self.last_poll_at < POLL_INTERVAL_SECONDS:
            return
        self.last_poll_at = now
        entries = _scan_downloads(self.download_dir)
        current_paths = {str(entry["path"]) for entry in entries}
        for stale_path in set(self.stable) - current_paths:
            self.stable.pop(stale_path, None)

        for entry in entries:
            signature = _entry_signature(entry)
            if signature in self.processed:
                continue
            path = Path(str(entry["path"]))
            key = str(path)
            size = int(entry["size"])
            mtime = float(entry["mtime"])
            last = self.stable.get(key)
            stable_count = last[2] + 1 if last and last[0] == size and last[1] == mtime else 1
            self.stable[key] = (size, mtime, stable_count)
            if stable_count < STABLE_POLLS:
                continue
            self.uploader(path, None)
            self.processed.add(signature)
            self.stable.pop(key, None)


def _scan_downloads(download_dir: Path) -> list[dict[str, Any]]:
    if not download_dir.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in download_dir.iterdir():
        if path.name.endswith(".crdownload") or not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append({
            "path": str(path),
            "name": path.name,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        })
    return entries


def _entry_signature(entry: dict[str, Any]) -> tuple[str, float, int]:
    return (str(entry["path"]), float(entry["mtime"]), int(entry["size"]))


class FileCaptureAgent:
    def __init__(
        self,
        *,
        backend_url: str,
        session_id: str,
        token: str,
        download_dir: Path,
        upload_func: Callable[[str, str, dict[str, str], Path, str, str], dict[str, Any]] | None = None,
        heartbeat_func: Callable[[str, str, dict[str, Any]], None] | None = None,
    ):
        self.backend_url = backend_url.rstrip("/")
        self.session_id = session_id
        self.token = token
        self.download_dir = download_dir
        self.upload_func = upload_func or _upload_multipart
        self.heartbeat_func = heartbeat_func or _post_json
        self.ws: Any = None
        self.next_id = 0
        self.responses: dict[int, dict[str, Any]] = {}
        self.downloads: dict[str, DownloadRecord] = {}
        self.last_heartbeat_at = 0.0
        self.fallback = DirectoryFallback(download_dir, self._upload_download)

    @property
    def ingest_url(self) -> str:
        return _build_url(self.backend_url, f"/api/sessions/{self.session_id}/files/ingest")

    @property
    def heartbeat_url(self) -> str:
        return _build_url(self.backend_url, f"/api/sessions/{self.session_id}/files/heartbeat")

    def connect(self, ws_url: str) -> None:
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=5)

    def _send(self, method: str, params: dict[str, Any] | None = None) -> int:
        if self.ws is None:
            raise RuntimeError("CDP websocket is not connected")
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params or {}}, separators=(",", ":")))
        return self.next_id

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
        import websocket

        req_id = self._send(method, params)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if req_id in self.responses:
                    msg = self.responses.pop(req_id)
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})
                if self.ws is None:
                    raise RuntimeError("CDP websocket closed")
                self.ws.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    raw = self.ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                self.handle_raw(raw)
            raise TimeoutError(f"{method} timed out")
        finally:
            if self.ws is not None:
                self.ws.settimeout(None)

    def bootstrap(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.fallback.prime()
        self.call(
            "Browser.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(self.download_dir),
                "eventsEnabled": True,
            },
            timeout=5,
        )
        self.send_heartbeat("running")
        _write_health({
            "agent": "file-capture-agent",
            "ok": True,
            "status": "running",
            "downloadDir": str(self.download_dir),
        })

    def loop(self) -> None:
        import websocket

        if self.ws is None:
            raise RuntimeError("CDP websocket is not connected")
        self.ws.settimeout(1.0)
        while True:
            self.maybe_heartbeat()
            self.fallback.maybe_poll()
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            self.handle_raw(raw)

    def handle_raw(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return
        self.handle_message(msg)

    def handle_message(self, msg: dict[str, Any]) -> None:
        if "id" in msg:
            self.responses[int(msg["id"])] = msg
            return

        method = msg.get("method")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if method == "Browser.downloadWillBegin":
            guid = str(params.get("guid") or "")
            if not guid:
                return
            self.downloads[guid] = DownloadRecord(
                guid=guid,
                suggested_filename=str(params.get("suggestedFilename") or ""),
                url=str(params.get("url") or ""),
            )
            self.send_heartbeat("running")
        elif method == "Browser.downloadProgress":
            self._handle_progress(params)

    def _handle_progress(self, params: dict[str, Any]) -> None:
        guid = str(params.get("guid") or "")
        state = str(params.get("state") or "")
        if not guid:
            return
        record = self.downloads.setdefault(guid, DownloadRecord(guid=guid))
        if params.get("filePath"):
            record.file_path = str(params["filePath"])
        record.updated_at = _now()
        if params.get("receivedBytes") is not None:
            try:
                record.received_bytes = max(0, int(params["receivedBytes"]))
            except (TypeError, ValueError):
                pass
        if params.get("totalBytes") is not None:
            try:
                record.total_bytes = max(0, int(params["totalBytes"]))
            except (TypeError, ValueError):
                pass
        if state == "completed" and not record.uploaded:
            path = self._resolve_completed_path(record)
            if path is None:
                self.send_heartbeat("degraded", "file_capture_completed_path_missing")
                return
            self._upload_download(path, guid)
            record.uploaded = True
            self.fallback.mark_processed(path)
            self.downloads.pop(guid, None)
            self.send_heartbeat("running")
        elif state == "canceled":
            self.downloads.pop(guid, None)
            self.send_heartbeat("running")
        else:
            self.send_heartbeat("running")

    def _resolve_completed_path(self, record: DownloadRecord) -> Path | None:
        candidates: list[Path] = []
        if record.file_path:
            candidates.append(Path(record.file_path))
        if record.suggested_filename:
            candidates.append(self.download_dir / _safe_filename(record.suggested_filename))
        for candidate in candidates:
            resolved = self._wait_for_file(candidate)
            if resolved is not None:
                return resolved
        return None

    def _wait_for_file(self, path: Path, timeout: float = 10.0) -> Path | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and path.is_file() and not path.name.endswith(".crdownload"):
                return path
            time.sleep(0.2)
        return None

    def _upload_download(self, path: Path, source_id: str | None) -> None:
        filename = _safe_filename(path.name)
        content_type = _content_type(path)
        stat = path.stat()
        sha256 = _sha256_file(path)
        fields = {
            "source": "browser_download",
            "sourceId": source_id or "",
            "originalName": filename,
            "contentType": content_type,
            "sizeBytes": str(stat.st_size),
            "sourcePath": str(path),
            "sourceMtime": str(stat.st_mtime),
            "sha256": sha256,
        }
        self.upload_func(self.ingest_url, self.token, fields, path, filename, content_type)
        self.send_heartbeat("running")
        _write_health({
            "agent": "file-capture-agent",
            "ok": True,
            "status": "running",
            "lastUploadedPath": str(path),
            "lastSourceId": source_id or "",
            "lastUploadedAt": _now(),
        })

    def maybe_heartbeat(self) -> None:
        if time.monotonic() - self.last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
            self.send_heartbeat("running")

    def active_downloads(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for record in self.downloads.values():
            if record.uploaded:
                continue
            name = record.suggested_filename or (Path(record.file_path).name if record.file_path else "download")
            percent = None
            if record.received_bytes is not None and record.total_bytes:
                percent = round(record.received_bytes / record.total_bytes * 100, 2)
            items.append({
                "id": record.guid,
                "name": _safe_filename(name),
                "sourceUrl": record.url,
                "contentType": _content_type(Path(name)),
                "receivedBytes": record.received_bytes,
                "totalBytes": record.total_bytes,
                "percent": percent,
                "startedAt": record.started_at,
                "updatedAt": record.updated_at,
            })
        return items

    def send_heartbeat(self, status: str, error: str = "") -> None:
        payload = {"status": status, "error": error[:300], "downloads": self.active_downloads()}
        try:
            self.heartbeat_func(self.heartbeat_url, self.token, payload)
            self.last_heartbeat_at = time.monotonic()
        except Exception as exc:
            _write_health({
                "agent": "file-capture-agent",
                "ok": False,
                "status": "heartbeat-error",
                "warnings": [str(exc)[:300]],
            })


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def main() -> None:
    try:
        backend_url = _required_env("BP_BACKEND_URL")
        session_id = _required_env("BP_SESSION_ID")
        token = _required_env("BP_FILE_CAPTURE_TOKEN")
        download_dir = Path(os.environ.get("BP_DOWNLOAD_DIR") or DEFAULT_DOWNLOAD_DIR)
    except Exception as exc:
        print(f"[file-capture-agent] disabled: {exc}", file=sys.stderr)
        _write_health({
            "agent": "file-capture-agent",
            "ok": False,
            "status": "missing-config",
            "warnings": [str(exc)],
        })
        return

    agent = FileCaptureAgent(
        backend_url=backend_url,
        session_id=session_id,
        token=token,
        download_dir=download_dir,
    )
    while True:
        try:
            _write_health({
                "agent": "file-capture-agent",
                "ok": False,
                "status": "starting",
                "downloadDir": str(download_dir),
            })
            ws_url = _discover_browser_ws()
            if not ws_url:
                raise RuntimeError("Chrome CDP browser websocket is unavailable")
            agent.connect(ws_url)
            agent.bootstrap()
            agent.loop()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[file-capture-agent] error: {exc}", file=sys.stderr)
            traceback.print_exc()
            agent.send_heartbeat("degraded", str(exc))
            _write_health({
                "agent": "file-capture-agent",
                "ok": False,
                "status": "degraded",
                "warnings": [str(exc)[:300]],
            })
            time.sleep(3)


if __name__ == "__main__":
    main()
