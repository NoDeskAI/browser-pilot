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


def test_browser_images_api_uses_edition_rejection_hook(monkeypatch):
    def reject():
        raise HTTPException(status_code=403, detail="browser_images_disabled")

    monkeypatch.setattr(browser_images, "reject_browser_images_api", reject)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(browser_images.list_images(_user()))

    assert exc.value.status_code == 403
    assert exc.value.detail == "browser_images_disabled"


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
        "runtime": "standard_chrome",
        "name": "",
        "chrome_major": major,
        "chrome_version": version,
        "base_image": f"selenium/standalone-chrome:{major}.0",
        "image_tag": tag,
        "status": status,
        "build_log": "",
        "created_at": created_at or datetime(2026, 5, 1, tzinfo=timezone.utc),
        "session_count": session_count,
    }


def _cloak_row(
    image_id,
    tag,
    *,
    name="Cloak Chromium Variant",
    status="ready",
    created_at=None,
    session_count=0,
    build_log="",
):
    return {
        "id": image_id,
        "runtime": "cloak_chromium",
        "name": name,
        "chrome_major": 0,
        "chrome_version": "",
        "base_image": "services/cloak-chromium-runtime",
        "image_tag": tag,
        "status": status,
        "build_log": build_log,
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
    assert cloak_images == []
    assert result["runtimeImages"] == cloak_images


def test_list_images_includes_multiple_cloak_runtime_images(monkeypatch):
    pool = FakePool(rows=[
        _cloak_row("cloak-a", "browser-pilot-cloak:login-a", name="Login A", session_count=3),
        _cloak_row("cloak-b", "browser-pilot-cloak:checkout-b", name="Checkout B", session_count=1),
    ])
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_image_created_at(_image_tag):
        return None

    monkeypatch.setattr(browser_images, "_docker_image_created_at", fake_image_created_at)

    result = asyncio.run(browser_images.list_images(_user()))
    cloak_images = [image for image in result["images"] if image["runtime"] == "cloak_chromium"]

    assert [image["id"] for image in cloak_images] == ["cloak-a", "cloak-b"]
    assert [image["name"] for image in cloak_images] == ["Login A", "Checkout B"]
    assert [image["sessionCount"] for image in cloak_images] == [3, 1]
    assert result["runtimeImages"] == cloak_images


def test_list_images_omits_missing_legacy_cloak_runtime_without_sessions(monkeypatch):
    pool = FakePool(rows=[])
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_image_created_at(_image_tag):
        return None

    monkeypatch.setattr(browser_images, "_docker_image_created_at", fake_image_created_at)

    result = asyncio.run(browser_images.list_images(_user()))

    assert [image for image in result["images"] if image["runtime"] == "cloak_chromium"] == []
    assert result["runtimeImages"] == []


@pytest.mark.parametrize(
    ("status", "stage", "progress"),
    [
        ("building", "building", 8),
        ("failed", "failed", 100),
    ],
)
def test_list_images_maps_cloak_runtime_build_state(monkeypatch, status, stage, progress):
    pool = FakePool(rows=[
        _cloak_row("cloak-build", "browser-pilot-cloak:build", status=status, build_log=f"{status} log")
    ])
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

    async def fake_start_cloak_runtime_build(*, user, image_name=""):
        called["count"] += 1
        assert user.tenant_id == "tenant-1"
        assert image_name == "Login Hardening"
        return {"id": "cloak-new", "status": "building"}

    monkeypatch.setattr(browser_images, "_start_cloak_runtime_build", fake_start_cloak_runtime_build)

    result = asyncio.run(
        browser_images.build_image(
            browser_images.BuildBody(runtime="cloak_chromium", imageName="Login Hardening"),
            _user(),
        )
    )

    assert called["count"] == 1
    assert result == {"id": "cloak-new", "status": "building"}


def test_build_cloak_runtime_creates_versioned_image_row(monkeypatch):
    pool = FakePool()
    queued = {"count": 0}

    async def fake_do_build(image_id, image_tag):
        assert image_id
        assert image_tag.startswith("browser-pilot-cloak:login-hardening-")
        raise AssertionError("build task should be queued, not awaited inline")

    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)
    monkeypatch.setattr(browser_images, "_do_build_cloak_runtime", fake_do_build)

    def fake_create_task(coro):
        queued["count"] += 1
        coro.close()
        return object()

    monkeypatch.setattr(browser_images.asyncio, "create_task", fake_create_task)

    result = asyncio.run(
        browser_images.build_image(
            browser_images.BuildBody(runtime="cloak_chromium", imageName="Login Hardening"),
            _user(),
        )
    )

    assert result["runtime"] == "cloak_chromium"
    assert result["name"] == "Login Hardening"
    assert result["status"] == "building"
    assert result["imageTag"].startswith("browser-pilot-cloak:login-hardening-")
    assert any("INSERT INTO browser_images" in call[0] and call[3] == "Login Hardening" for call in pool.executed)
    assert queued["count"] == 1


def test_delete_cloak_runtime_removes_selected_image(monkeypatch):
    pool = FakePool(
        duplicate={"image_tag": "browser-pilot-cloak:login-a", "chrome_version": "", "runtime": "cloak_chromium"},
        fetchval_result=0,
    )
    commands = []
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_run(cmd, timeout=30):
        commands.append((cmd, timeout))
        return "", "", 0

    monkeypatch.setattr(browser_images, "_run", fake_run)

    result = asyncio.run(browser_images.delete_image("cloak-a", _user()))

    assert result == {"ok": True}
    assert commands == [("docker rmi browser-pilot-cloak:login-a", 30)]
    assert any("browser_image_id = $2" in call[0] for call in pool.fetches)


def test_delete_cloak_runtime_rejects_in_use_image(monkeypatch):
    pool = FakePool(
        duplicate={"image_tag": "browser-pilot-cloak:login-a", "chrome_version": "", "runtime": "cloak_chromium"},
        fetchval_result=2,
    )
    commands = []
    monkeypatch.setattr(browser_images, "get_pool", lambda: pool)

    async def fake_run(cmd, timeout=30):
        commands.append((cmd, timeout))
        return "", "", 0

    monkeypatch.setattr(browser_images, "_run", fake_run)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(browser_images.delete_image("cloak-a", _user()))

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
