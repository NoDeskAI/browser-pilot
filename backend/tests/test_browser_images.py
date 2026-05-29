import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routes import browser_images


class FakePool:
    def __init__(self, duplicate=None, rows=None, fetchval_result=0):
        self.duplicate = duplicate
        self.rows = rows or []
        self.fetchval_result = fetchval_result
        self.executed = []
        self.fetches = []

    def acquire(self):
        return self

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, *args):
        self.executed.append(args)
        return "OK"

    async def fetchrow(self, *args):
        self.fetches.append(args)
        return self.duplicate

    async def fetch(self, *args):
        self.fetches.append(args)
        return self.rows

    async def fetchval(self, *args):
        self.fetches.append(args)
        return self.fetchval_result


def _user():
    return SimpleNamespace(id="user-1", tenant_id="tenant-1", role="admin")


def _reset_cloak_state(monkeypatch, **updates):
    state = {
        "status": "",
        "build_log": "",
        "created_at": None,
        "started_at": None,
        "updated_at": None,
        "stage": "",
        "progress": 0,
    }
    state.update(updates)
    monkeypatch.setattr(browser_images, "_cloak_build_state", state)


def _image_row(
    image_id,
    major,
    version,
    tag,
    *,
    status="ready",
    created_at=None,
    session_count=0,
):
    return {
        "id": image_id,
        "chrome_major": major,
        "chrome_version": version,
        "base_image": f"selenium/standalone-chrome:{major}.0",
        "image_tag": tag,
        "status": status,
        "build_log": "",
        "created_at": created_at or datetime(2026, 5, 1, tzinfo=timezone.utc),
        "session_count": session_count,
    }


def test_build_marks_duplicate_full_chrome_version_failed(monkeypatch):
    pool = FakePool({"id": "existing", "image_tag": "browser-pilot-selenium:chrome-147-old"})
    commands = []
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_run(cmd, timeout=600):
        commands.append(cmd)
        if "chromium --version" in cmd:
            return "Chromium 147.0.7727.55", "", 0
        return "", "", 0

    monkeypatch.setattr(browser_images, "_run", fake_run)

    asyncio.run(
        browser_images._do_build(
            "new-image",
            "tenant-1",
            "selenium/standalone-chrome:147.0",
            "browser-pilot-selenium:chrome-147.0-new",
            147,
        )
    )

    assert any(
        "SET status = 'failed', build_log" in call[0]
        and call[1] == "Chrome 147.0.7727.55 is already built."
        and call[2] == "new-image"
        for call in pool.executed
    )
    assert not any("SET status = 'ready'" in call[0] for call in pool.executed)
    assert "docker rmi browser-pilot-selenium:chrome-147.0-new" in commands


def test_list_images_returns_one_canonical_image_per_chrome_major(monkeypatch):
    _reset_cloak_state(monkeypatch)
    rows = [
        _image_row("147-fp", 147, "147.0.7727.55", "browser-pilot-selenium:chrome-147-fpagent"),
        _image_row("147", 147, "147.0.7727.55", "browser-pilot-selenium:chrome-147"),
        _image_row("135", 135, "135.0.7049.84", "browser-pilot-selenium:chrome-135", session_count=2),
        _image_row("135-fp", 135, "135.0.7049.84", "browser-pilot-selenium:chrome-135-fpagent"),
    ]
    pool = FakePool(rows=rows)
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_image_created_at(_image_tag):
        return None

    monkeypatch.setattr(browser_images, "_docker_image_created_at", fake_image_created_at)

    result = asyncio.run(browser_images.list_images(_user()))
    standard_images = [image for image in result["images"] if image["runtime"] == "standard_chrome"]
    cloak_images = [image for image in result["images"] if image["runtime"] == "cloak_chromium"]

    assert [image["chromeMajor"] for image in standard_images] == [147, 135]
    assert [image["imageTag"] for image in standard_images] == [
        "browser-pilot-selenium:chrome-147",
        "browser-pilot-selenium:chrome-135",
    ]
    assert len(cloak_images) == 1
    assert result["runtimeImages"] == cloak_images


def test_list_images_includes_ready_cloak_runtime_with_session_count(monkeypatch):
    _reset_cloak_state(monkeypatch)
    pool = FakePool(rows=[], fetchval_result=3)
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_image_created_at(image_tag):
        if image_tag == browser_images.CLOAK_BROWSER_IMAGE_NAME:
            return "2026-05-13T23:14:17.41712077+08:00"
        return None

    monkeypatch.setattr(browser_images, "_docker_image_created_at", fake_image_created_at)

    result = asyncio.run(browser_images.list_images(_user()))
    cloak = next(image for image in result["images"] if image["runtime"] == "cloak_chromium")

    assert cloak["id"] == "cloak_chromium"
    assert cloak["name"] == "Cloak Chromium"
    assert cloak["status"] == "ready"
    assert cloak["createdAt"] == "2026-05-13T23:14:17.41712077+08:00"
    assert cloak["sessionCount"] == 3
    assert cloak["buildProgress"]["progress"] == 100
    assert result["runtimeImages"] == [cloak]
    assert any("browser_runtime = 'cloak_chromium'" in call[0] for call in pool.fetches)


def test_list_images_reports_missing_cloak_runtime(monkeypatch):
    _reset_cloak_state(monkeypatch)
    pool = FakePool(rows=[])
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_image_created_at(_image_tag):
        return None

    monkeypatch.setattr(browser_images, "_docker_image_created_at", fake_image_created_at)

    result = asyncio.run(browser_images.list_images(_user()))
    cloak = next(image for image in result["images"] if image["runtime"] == "cloak_chromium")

    assert cloak["status"] == "missing"
    assert cloak["buildProgress"]["stage"] == "missing"
    assert cloak["sessionCount"] == 0


@pytest.mark.parametrize(
    ("status", "stage", "progress"),
    [
        ("building", "pulling_base_image", 8),
        ("failed", "failed", 100),
    ],
)
def test_list_images_maps_cloak_runtime_build_state(monkeypatch, status, stage, progress):
    _reset_cloak_state(
        monkeypatch,
        status=status,
        stage=stage,
        progress=progress,
        build_log=f"{status} log",
        started_at=1,
        updated_at="2026-05-01T00:00:00+00:00",
    )
    pool = FakePool(rows=[])
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_image_created_at(_image_tag):
        return None

    monkeypatch.setattr(browser_images, "_docker_image_created_at", fake_image_created_at)

    result = asyncio.run(browser_images.list_images(_user()))
    cloak = next(image for image in result["images"] if image["runtime"] == "cloak_chromium")

    assert cloak["status"] == status
    assert cloak["buildLog"] == f"{status} log"
    assert cloak["buildProgress"]["stage"] == stage
    assert cloak["buildProgress"]["progress"] >= progress


def test_build_allows_different_full_chrome_version(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_run(cmd, timeout=600):
        if "chromium --version" in cmd:
            return "Chromium 147.0.7727.60", "", 0
        return "", "", 0

    monkeypatch.setattr(browser_images, "_run", fake_run)

    asyncio.run(
        browser_images._do_build(
            "new-image",
            "tenant-1",
            "selenium/standalone-chrome:147.0.7727.60",
            "browser-pilot-selenium:chrome-147.0.7727.60-new",
            147,
            chrome_version="147.0.7727.60",
        )
    )

    assert any(
        "SET status = 'ready'" in call[0]
        and call[1] == "147.0.7727.60"
        for call in pool.executed
    )


def test_build_rejects_exact_ready_version_before_start(monkeypatch):
    pool = FakePool({"id": "existing", "status": "ready"})
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)
    monkeypatch.setattr(browser_images, "_check_tag_exists", lambda *_args: asyncio.sleep(0, True))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            browser_images.build_image(
                browser_images.BuildBody(chromeVersion="147.0.7727.55"),
                _user(),
            )
        )

    assert exc.value.status_code == 409
    assert "147.0.7727.55" in exc.value.detail
    assert not any("INSERT INTO browser_images" in call[0] for call in pool.executed)


def test_build_cloak_runtime_uses_existing_build_path(monkeypatch):
    called = {"count": 0}

    async def fake_start_cloak_runtime_build():
        called["count"] += 1
        return {"id": "cloak_chromium", "status": "pending"}

    monkeypatch.setattr(browser_images, "_start_cloak_runtime_build", fake_start_cloak_runtime_build)

    result = asyncio.run(
        browser_images.build_image(
            browser_images.BuildBody(runtime="cloak_chromium"),
            _user(),
        )
    )

    assert called["count"] == 1
    assert result == {"id": "cloak_chromium", "status": "pending"}


def test_delete_cloak_runtime_removes_local_image_and_resets_state(monkeypatch):
    _reset_cloak_state(
        monkeypatch,
        status="ready",
        build_log="Image is available locally.",
        created_at="2026-05-01T00:00:00+00:00",
        started_at=1,
        updated_at="2026-05-01T00:00:00+00:00",
        stage="ready",
        progress=100,
    )
    pool = FakePool(fetchval_result=0)
    commands = []
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_run(cmd, timeout=30):
        commands.append((cmd, timeout))
        return "", "", 0

    monkeypatch.setattr(browser_images, "_run", fake_run)

    result = asyncio.run(browser_images.delete_image("cloak_chromium", _user()))

    assert result == {"ok": True}
    assert commands == [(f"docker rmi {browser_images.CLOAK_BROWSER_IMAGE_NAME}", 30)]
    assert browser_images._cloak_build_state["status"] == ""
    assert browser_images._cloak_build_state["build_log"] == ""
    assert browser_images._cloak_build_state["created_at"] is None
    assert any("browser_runtime = 'cloak_chromium'" in call[0] for call in pool.fetches)


def test_delete_cloak_runtime_rejects_in_use_image(monkeypatch):
    _reset_cloak_state(monkeypatch)
    pool = FakePool(fetchval_result=2)
    commands = []
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_run(cmd, timeout=30):
        commands.append((cmd, timeout))
        return "", "", 0

    monkeypatch.setattr(browser_images, "_run", fake_run)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(browser_images.delete_image("cloak_chromium", _user()))

    assert exc.value.status_code == 409
    assert "2 session(s)" in exc.value.detail
    assert commands == []


def test_build_rejects_duplicate_in_progress_base_image(monkeypatch):
    pool = FakePool({"id": "existing", "status": "building"})
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)
    monkeypatch.setattr(browser_images, "_check_tag_exists", lambda *_args: asyncio.sleep(0, True))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            browser_images.build_image(
                browser_images.BuildBody(chromeVersion="147"),
                _user(),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "This version is already being built."
    assert not any("INSERT INTO browser_images" in call[0] for call in pool.executed)
