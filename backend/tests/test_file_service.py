import asyncio
from datetime import datetime, timezone

import pytest

from app.auth.dependencies import CurrentUser
from app import file_service


def _user(*, tenant_id="tenant-1", session_scope=None):
    return CurrentUser(
        id="user-1",
        tenant_id=tenant_id,
        email="user@example.com",
        name="User",
        role="admin",
        created_at="2026-05-12T00:00:00Z",
        session_scope=session_scope,
    )


class FakeStore:
    storage_name = "s3"

    def __init__(self):
        self.objects = {}

    async def save_bytes(self, data, *, key, content_type):
        self.objects[key] = (data, content_type)

    async def save_file(self, path, *, key, content_type):
        self.objects[key] = (path.read_bytes(), content_type)

    async def get_by_key(self, key):
        return self.objects.get(key)


class FakePool:
    def __init__(self):
        self.rows = {}
        self.session_tenants = {"session-1": "tenant-1", "session-2": "tenant-2"}

    async def fetchrow(self, query, *args):
        if "SELECT tenant_id FROM sessions" in query:
            tenant = self.session_tenants.get(args[0])
            return {"tenant_id": tenant} if tenant else None
        if "SELECT * FROM session_files WHERE id" in query:
            return self.rows.get(args[0])
        if "SELECT * FROM session_files" in query and "source_path" in query:
            session_id, source, source_path, source_mtime, size_bytes = args
            for row in self.rows.values():
                if (
                    row["session_id"] == session_id
                    and row["source"] == source
                    and row["source_path"] == source_path
                    and row["source_mtime"] == source_mtime
                    and row["size_bytes"] == size_bytes
                ):
                    return row
            return None
        return None

    async def execute(self, query, *args):
        if "INSERT INTO session_files" not in query:
            return "OK"
        (
            file_id,
            session_id,
            tenant_id,
            source,
            filename,
            content_type,
            size_bytes,
            storage,
            object_key,
            source_path,
            source_mtime,
        ) = args
        self.rows[file_id] = {
            "id": file_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "source": source,
            "original_name": filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "storage": storage,
            "object_key": object_key,
            "source_path": source_path,
            "source_mtime": source_mtime,
            "created_at": datetime(2026, 5, 12, tzinfo=timezone.utc),
        }
        return "INSERT 0 1"

    async def fetch(self, query, *args):
        if "FROM session_files" not in query:
            return []
        return [row for row in self.rows.values() if row["session_id"] == args[0]]


def test_save_bytes_creates_file_record_and_backend_url(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)
    monkeypatch.setattr(file_service, "API_BASE_URL", "http://localhost:8000")

    result = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="screenshot",
            data=b"png-bytes",
            filename="screenshot.png",
            content_type="image/png",
        )
    )

    row = pool.rows[result["id"]]
    assert result["url"] == f"http://localhost:8000/api/files/{result['id']}.png"
    assert "object-storage:9000" not in result["url"]
    assert row["object_key"] == f"files/session-1/{result['id']}/screenshot.png"
    assert store.objects[row["object_key"]] == (b"png-bytes", "image/png")


def test_save_file_dedupes_browser_download_by_source_metadata(monkeypatch, tmp_path):
    store = FakeStore()
    pool = FakePool()
    downloaded = tmp_path / "report.txt"
    downloaded.write_text("hello")
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)

    first = asyncio.run(
        file_service.save_file(
            session_id="session-1",
            source="browser_download",
            path=downloaded,
            filename="report.txt",
            source_path="/home/seluser/Downloads/report.txt",
            source_mtime=123.0,
        )
    )
    second = asyncio.run(
        file_service.save_file(
            session_id="session-1",
            source="browser_download",
            path=downloaded,
            filename="report.txt",
            source_path="/home/seluser/Downloads/report.txt",
            source_mtime=123.0,
        )
    )

    assert second["id"] == first["id"]
    assert len(pool.rows) == 1
    assert len(store.objects) == 1


def test_get_file_payload_enforces_tenant_and_session_scope(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)

    result = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="screenshot",
            data=b"png-bytes",
            filename="screenshot.png",
        )
    )

    assert asyncio.run(file_service.get_file_payload(result["id"], _user())) == (b"png-bytes", "image/png")
    assert asyncio.run(file_service.get_file_payload(result["id"], _user(session_scope="session-1"))) == (b"png-bytes", "image/png")

    with pytest.raises(file_service.HTTPException) as tenant_exc:
        asyncio.run(file_service.get_file_payload(result["id"], _user(tenant_id="tenant-2")))
    assert tenant_exc.value.status_code == 404

    with pytest.raises(file_service.HTTPException) as scope_exc:
        asyncio.run(file_service.get_file_payload(result["id"], _user(session_scope="session-2")))
    assert scope_exc.value.status_code == 403


def test_list_session_files_returns_file_dtos(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)

    saved = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="screenshot",
            data=b"png-bytes",
            filename="screenshot.png",
        )
    )

    files = asyncio.run(file_service.list_session_files("session-1"))
    assert files == [saved]
