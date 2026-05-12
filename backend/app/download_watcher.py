from __future__ import annotations

import asyncio
import json
import logging
import shlex
import tempfile
from pathlib import Path
from typing import Any

from app.container import _run, container_name

logger = logging.getLogger("download_watcher")

DOWNLOAD_DIR = "/home/seluser/Downloads"
POLL_INTERVAL_SECONDS = 1.0
STABLE_POLLS = 2

_watchers: dict[str, asyncio.Task] = {}
_configured_download_behavior: set[str] = set()


def start_download_watcher(session_id: str) -> None:
    task = _watchers.get(session_id)
    if task and not task.done():
        return
    _watchers[session_id] = asyncio.create_task(_watch_downloads(session_id))
    logger.info("Download watcher started for session %s", session_id)


async def stop_download_watcher(session_id: str) -> None:
    _configured_download_behavior.discard(session_id)
    task = _watchers.pop(session_id, None)
    if not task:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Download watcher stopped for session %s", session_id)


async def stop_all_download_watchers() -> None:
    for session_id in list(_watchers):
        await stop_download_watcher(session_id)


async def configure_download_behavior_for_webdriver(
    session_id: str,
    webdriver_session_id: str,
    selenium_base_url: str,
) -> None:
    if session_id in _configured_download_behavior:
        return
    from app.tools.browser.session import wd_fetch

    await wd_fetch(
        f"/session/{webdriver_session_id}/goog/cdp/execute",
        "POST",
        {
            "cmd": "Browser.setDownloadBehavior",
            "params": {
                "behavior": "allow",
                "downloadPath": DOWNLOAD_DIR,
                "eventsEnabled": False,
            },
        },
        timeout=5,
        base_url=selenium_base_url,
    )
    _configured_download_behavior.add(session_id)
    logger.info("Chrome download behavior configured for session %s -> %s", session_id, DOWNLOAD_DIR)


async def configure_download_behavior(session_id: str) -> None:
    from app.tools.browser.session import browser_session

    async with browser_session(session_id) as (sid, base):
        await configure_download_behavior_for_webdriver(session_id, sid, base)


async def _watch_downloads(session_id: str) -> None:
    processed: set[tuple[str, float, int]] = set()
    stable: dict[str, tuple[int, float, int]] = {}
    try:
        for item in await _list_downloads(session_id):
            processed.add(_entry_signature(item))

        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            entries = await _list_downloads(session_id)
            current_paths = {str(entry["path"]) for entry in entries}
            for stale_path in set(stable) - current_paths:
                stable.pop(stale_path, None)

            for entry in entries:
                signature = _entry_signature(entry)
                if signature in processed:
                    continue
                key = str(entry["path"])
                size = int(entry["size"])
                mtime = float(entry["mtime"])
                last = stable.get(key)
                stable_count = last[2] + 1 if last and last[0] == size and last[1] == mtime else 1
                stable[key] = (size, mtime, stable_count)
                if stable_count < STABLE_POLLS:
                    continue
                uploaded = await _upload_download(session_id, entry)
                processed.add(signature)
                stable.pop(key, None)
                logger.info(
                    "Browser download captured session=%s file=%s",
                    session_id,
                    uploaded.get("id") if uploaded else "",
                )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Download watcher exited for session %s: %s", session_id, exc)


def _entry_signature(entry: dict[str, Any]) -> tuple[str, float, int]:
    return (str(entry["path"]), float(entry["mtime"]), int(entry["size"]))


async def _list_downloads(session_id: str) -> list[dict[str, Any]]:
    name = container_name(session_id)
    script = r"""
import json, os
root = "/home/seluser/Downloads"
items = []
if os.path.isdir(root):
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if name.endswith(".crdownload") or not os.path.isfile(path):
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        items.append({"path": path, "name": name, "size": st.st_size, "mtime": st.st_mtime})
print(json.dumps(items, ensure_ascii=False))
"""
    cmd = f"docker exec {shlex.quote(name)} python3 -c {shlex.quote(script)}"
    stdout, stderr, rc = await _run(cmd, timeout=5)
    if rc != 0:
        if "No such container" in stderr or "is not running" in stderr:
            raise RuntimeError(stderr.strip() or f"{name} is not running")
        logger.debug("List downloads failed for %s: %s", session_id, stderr[:200])
        return []
    try:
        data = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        logger.debug("List downloads returned invalid JSON for %s: %s", session_id, stdout[:200])
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data if _valid_entry(entry)]


def _valid_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if str(entry.get("name") or "").endswith(".crdownload"):
        return False
    return bool(entry.get("path") and entry.get("name") and entry.get("size") is not None and entry.get("mtime") is not None)


async def _upload_download(session_id: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    name = container_name(session_id)
    source_path = str(entry["path"])
    filename = Path(str(entry["name"])).name
    with tempfile.TemporaryDirectory(prefix="bp-download-") as tmp:
        local_path = Path(tmp) / filename
        cmd = f"docker cp {shlex.quote(name)}:{shlex.quote(source_path)} {shlex.quote(str(local_path))}"
        _stdout, stderr, rc = await _run(cmd, timeout=30)
        if rc != 0:
            logger.warning("docker cp download failed session=%s path=%s: %s", session_id, source_path, stderr[:200])
            return None

        from app.file_service import save_file

        return await save_file(
            session_id=session_id,
            source="browser_download",
            path=local_path,
            filename=filename,
            source_path=source_path,
            source_mtime=float(entry["mtime"]),
        )
