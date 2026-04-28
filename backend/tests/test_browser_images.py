import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routes import browser_images


class FakePool:
    def __init__(self, duplicate=None):
        self.duplicate = duplicate
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


def _user():
    return SimpleNamespace(id="user-1", tenant_id="tenant-1", role="admin")


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
