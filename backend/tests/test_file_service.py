import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.auth.dependencies import CurrentUser
from app import file_capture
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
        self.fail_delete = False

    async def save_bytes(self, data, *, key, content_type):
        self.objects[key] = (data, content_type)

    async def save_file(self, path, *, key, content_type):
        self.objects[key] = (path.read_bytes(), content_type)

    async def get_by_key(self, key):
        return self.objects.get(key)

    async def delete_by_key(self, key):
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.objects.pop(key, None)


class FakePool:
    def __init__(self):
        self.rows = {}
        self.session_tenants = {"session-1": "tenant-1", "session-2": "tenant-2"}
        self._created_count = 0

    async def fetchrow(self, query, *args):
        if "UPDATE session_files" in query and "SET original_name" in query:
            session_id, file_id, filename = args
            row = self.rows.get(file_id)
            if row and row["session_id"] == session_id:
                row["original_name"] = filename
                return row
            return None
        if "SELECT tenant_id FROM sessions" in query:
            tenant = self.session_tenants.get(args[0])
            return {"tenant_id": tenant} if tenant else None
        if "SELECT * FROM session_files WHERE id" in query:
            return self.rows.get(args[0])
        if "SELECT * FROM session_files WHERE session_id = $1 AND id = $2" in query:
            session_id, file_id = args
            row = self.rows.get(file_id)
            if row and row["session_id"] == session_id:
                return row
            return None
        if "SELECT * FROM session_files" in query and "source_id = $3" in query:
            session_id, source, source_id = args
            for row in self.rows.values():
                if (
                    row["session_id"] == session_id
                    and row["source"] == source
                    and row.get("source_id") == source_id
                ):
                    return row
            return None
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
        if "DELETE FROM session_files" in query:
            session_id, file_id = args
            row = self.rows.get(file_id)
            if row and row["session_id"] == session_id:
                del self.rows[file_id]
                return "DELETE 1"
            return "DELETE 0"
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
            source_id,
            source_path,
            source_mtime,
            sha256,
        ) = args
        created_at = datetime(2026, 5, 12, tzinfo=timezone.utc) + timedelta(seconds=self._created_count)
        self._created_count += 1
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
            "source_id": source_id,
            "source_path": source_path,
            "source_mtime": source_mtime,
            "sha256": sha256,
            "uploaded_at": created_at,
            "created_at": created_at,
        }
        return "INSERT 0 1"

    async def fetch(self, query, *args):
        if "FROM session_files" not in query:
            return []
        rows = [row for row in self.rows.values() if row["session_id"] == args[0]]
        rows.sort(key=lambda row: (row["created_at"], row["id"]), reverse="DESC" in query)
        return rows


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


def test_save_file_dedupes_browser_download_by_source_id(monkeypatch, tmp_path):
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
            source_id="download-guid-1",
        )
    )
    renamed = tmp_path / "report-renamed.txt"
    renamed.write_text("hello")
    second = asyncio.run(
        file_service.save_file(
            session_id="session-1",
            source="browser_download",
            path=renamed,
            filename="report-renamed.txt",
            source_id="download-guid-1",
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
    assert files[0]["status"] == "completed"


def test_list_session_files_merges_active_downloads_and_completed_files(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)
    monkeypatch.setattr(file_capture, "list_active_downloads", lambda session_id: [
        {
            "id": "download-guid-1",
            "name": "report.pdf",
            "status": "downloading",
            "source": "browser_download",
            "url": None,
            "contentType": "application/pdf",
            "size": None,
            "receivedBytes": 100,
            "totalBytes": 400,
            "percent": 25.0,
            "startedAt": "2026-05-12T00:00:00Z",
            "updatedAt": "2026-05-12T00:00:01Z",
        }
    ])

    completed = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="screenshot",
            data=b"png-bytes",
            filename="screenshot.png",
        )
    )

    files = asyncio.run(file_service.list_session_files("session-1"))

    assert [item["status"] for item in files] == ["downloading", "completed"]
    assert files[0]["id"] == "download-guid-1"
    assert files[0]["url"] is None
    assert files[0]["percent"] == 25.0
    assert files[1] == completed


def test_list_session_files_skips_active_download_that_already_completed(monkeypatch, tmp_path):
    store = FakeStore()
    pool = FakePool()
    downloaded = tmp_path / "report.txt"
    downloaded.write_text("hello")
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)
    monkeypatch.setattr(file_capture, "list_active_downloads", lambda session_id: [
        {
            "id": "download-guid-1",
            "name": "report.txt",
            "status": "downloading",
            "source": "browser_download",
            "url": None,
        }
    ])

    completed = asyncio.run(
        file_service.save_file(
            session_id="session-1",
            source="browser_download",
            path=downloaded,
            filename="report.txt",
            source_id="download-guid-1",
        )
    )

    files = asyncio.run(file_service.list_session_files("session-1"))

    assert files == [completed]
    assert files[0]["status"] == "completed"


def test_session_file_metadata_rename_and_delete(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)
    monkeypatch.setattr(file_service, "API_BASE_URL", "http://localhost:8000")

    saved = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="user_upload",
            data=b"csv-bytes",
            filename="report.csv",
            content_type="text/csv",
        )
    )
    object_key = pool.rows[saved["id"]]["object_key"]

    loaded = asyncio.run(file_service.get_session_file("session-1", saved["id"]))
    renamed = asyncio.run(file_service.rename_session_file("session-1", saved["id"], "../final report.txt"))

    assert loaded["id"] == saved["id"]
    assert renamed["name"] == "final report.txt"
    assert renamed["url"] == f"http://localhost:8000/api/files/{saved['id']}.txt"
    assert pool.rows[saved["id"]]["object_key"] == object_key

    deleted = asyncio.run(file_service.delete_session_file("session-1", saved["id"]))

    assert deleted == {
        "ok": True,
        "objectDeleted": True,
        "recordDeleted": True,
        "warning": None,
    }
    assert saved["id"] not in pool.rows
    assert object_key not in store.objects


def test_session_file_operations_require_matching_session(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)

    saved = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="user_upload",
            data=b"data",
            filename="a.txt",
        )
    )

    with pytest.raises(file_service.HTTPException) as get_exc:
        asyncio.run(file_service.get_session_file("session-2", saved["id"]))
    with pytest.raises(file_service.HTTPException) as rename_exc:
        asyncio.run(file_service.rename_session_file("session-2", saved["id"], "b.txt"))
    with pytest.raises(file_service.HTTPException) as delete_exc:
        asyncio.run(file_service.delete_session_file("session-2", saved["id"]))

    assert get_exc.value.status_code == 404
    assert rename_exc.value.status_code == 404
    assert delete_exc.value.status_code == 404
    assert saved["id"] in pool.rows


def test_delete_session_file_removes_record_when_object_delete_fails(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)

    saved = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="user_upload",
            data=b"data",
            filename="a.txt",
        )
    )
    store.fail_delete = True

    deleted = asyncio.run(file_service.delete_session_file("session-1", saved["id"]))

    assert deleted == {
        "ok": True,
        "objectDeleted": False,
        "recordDeleted": True,
        "warning": "file_object_delete_failed",
    }
    assert saved["id"] not in pool.rows
