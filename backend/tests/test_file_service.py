import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.auth.dependencies import CurrentUser
from app import file_capture
from app import file_service
from app.file_urls import verify_file_download_signature


def _user(*, user_id="user-1", tenant_id="tenant-1", role="admin", session_scope=None):
    return CurrentUser(
        id=user_id,
        tenant_id=tenant_id,
        email="user@example.com",
        name="User",
        role=role,
        created_at="2026-05-12T00:00:00Z",
        session_scope=session_scope,
    )


class FakeStore:
    storage_name = "builtin"

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


class FakePresignedStore(FakeStore):
    storage_name = "s3"

    def __init__(self):
        super().__init__()
        self.download_call = None

    async def download_url(self, **kwargs):
        self.download_call = kwargs
        return f"http://public-storage:9000/{kwargs['key']}?ttl={kwargs['expires_in']}"


class FakePool:
    def __init__(self):
        self.rows = {}
        self.sessions = {
            "session-1": {"tenant_id": "tenant-1", "user_id": "user-1", "name": "Session 1"},
            "session-2": {"tenant_id": "tenant-2", "user_id": "user-2", "name": "Session 2"},
            "session-3": {"tenant_id": "tenant-1", "user_id": "user-2", "name": "Session 3"},
        }
        self._created_count = 0

    async def fetchrow(self, query, *args):
        if "UPDATE session_files" in query and "SET original_name = $2" in query:
            file_id, filename = args
            row = self.rows.get(file_id)
            if row:
                row["original_name"] = filename
                return row
            return None
        if "UPDATE session_files" in query and "SET original_name" in query:
            session_id, file_id, filename = args
            row = self.rows.get(file_id)
            if row and row["session_id"] == session_id:
                row["original_name"] = filename
                return row
            return None
        if "FROM sessions WHERE id = $1" in query:
            session = self.sessions.get(args[0])
            if not session:
                return None
            if "name" in query:
                return session
            if "user_id" in query:
                return {"tenant_id": session["tenant_id"], "user_id": session["user_id"]}
            return {"tenant_id": session["tenant_id"]}
        if "SELECT * FROM session_files WHERE session_id = $1 AND id = $2" in query:
            session_id, file_id = args
            row = self.rows.get(file_id)
            if row and row["session_id"] == session_id:
                return row
            return None
        if "SELECT * FROM session_files WHERE id" in query:
            return self.rows.get(args[0])
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
        if "UPDATE session_files" in query and "archived_session_id" in query:
            session_id, session_name, tenant_id, user_id, archive_ids = args
            now = datetime(2026, 5, 13, tzinfo=timezone.utc)
            count = 0
            for file_id in archive_ids:
                row = self.rows.get(file_id)
                if row and row["session_id"] == session_id:
                    row["session_id"] = None
                    row["archived_at"] = now
                    row["archived_session_id"] = session_id
                    row["archived_session_name"] = session_name
                    row["tenant_id"] = row.get("tenant_id") or tenant_id
                    row["user_id"] = row.get("user_id") or user_id
                    count += 1
            return f"UPDATE {count}"
        if "DELETE FROM session_files WHERE id = $1" in query:
            file_id = args[0]
            if file_id in self.rows:
                del self.rows[file_id]
                return "DELETE 1"
            return "DELETE 0"
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
            user_id,
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
            "user_id": user_id,
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
            "archived_at": None,
            "archived_session_id": None,
            "archived_session_name": None,
            "created_at": created_at,
        }
        return "INSERT 0 1"

    async def fetch(self, query, *args):
        if "FROM session_files" not in query:
            return []
        if "WHERE tenant_id = $1 AND user_id = $2" in query:
            rows = [row for row in self.rows.values() if row.get("tenant_id") == args[0] and row.get("user_id") == args[1]]
        elif "WHERE tenant_id = $1" in query:
            rows = [row for row in self.rows.values() if row.get("tenant_id") == args[0]]
        else:
            rows = [row for row in self.rows.values() if row["session_id"] == args[0]]
        rows.sort(key=lambda row: (row["created_at"], row["id"]), reverse="DESC" in query)
        return rows


def test_save_bytes_creates_file_record_and_signed_backend_url(monkeypatch):
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
            content_type="image/png",
        )
    )

    row = pool.rows[result["id"]]
    prefix = f"http://localhost:8000/api/files/{result['id']}.png?"
    assert result["url"].startswith(prefix)
    query = result["url"].split("?", 1)[1]
    parts = dict(item.split("=", 1) for item in query.split("&"))
    assert verify_file_download_signature(
        result["id"],
        "png",
        int(parts["expires"]),
        parts["signature"],
    )
    assert "object-storage:9000" not in result["url"]
    assert row["object_key"] == f"files/session-1/{result['id']}/screenshot.png"
    assert store.objects[row["object_key"]] == (b"png-bytes", "image/png")


def test_save_bytes_returns_s3_presigned_public_url(monkeypatch):
    store = FakePresignedStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)

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
    assert result["url"] == f"http://public-storage:9000/{row['object_key']}?ttl={file_service.FILE_DOWNLOAD_URL_TTL_SECONDS}"
    assert store.download_call == {
        "key": row["object_key"],
        "file_id": result["id"],
        "filename": "screenshot.png",
        "expires_in": file_service.FILE_DOWNLOAD_URL_TTL_SECONDS,
    }


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


def test_get_file_payload_rejects_archived_file_for_session_scoped_token(monkeypatch):
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
    result = asyncio.run(
        file_service.handle_session_delete_files(
            "session-1",
            _user(),
            file_delete_mode="none",
        )
    )

    row = pool.rows[saved["id"]]
    assert result["archivedFileIds"] == [saved["id"]]
    assert row["session_id"] is None
    assert row["archived_session_id"] == "session-1"
    assert row["archived_session_name"] == "Session 1"
    assert asyncio.run(file_service.get_file_payload(saved["id"], _user())) == (b"png-bytes", "image/png")

    with pytest.raises(file_service.HTTPException) as scope_exc:
        asyncio.run(file_service.get_file_payload(saved["id"], _user(session_scope="session-1")))
    assert scope_exc.value.status_code == 403


def test_handle_session_delete_files_selected_archives_unselected_and_warns_on_object_delete(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)

    keep = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="user_upload",
            data=b"keep",
            filename="keep.txt",
        )
    )
    remove = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="user_upload",
            data=b"remove",
            filename="remove.txt",
        )
    )
    remove_key = pool.rows[remove["id"]]["object_key"]
    store.fail_delete = True

    result = asyncio.run(
        file_service.handle_session_delete_files(
            "session-1",
            _user(),
            file_delete_mode="selected",
            delete_file_ids=[remove["id"]],
        )
    )

    assert result["deletedFileIds"] == [remove["id"]]
    assert result["archivedFileIds"] == [keep["id"]]
    assert result["objectDeleteFailedFileIds"] == [remove["id"]]
    assert result["warning"] == "file_object_delete_failed"
    assert remove["id"] not in pool.rows
    assert remove_key in store.objects
    assert pool.rows[keep["id"]]["session_id"] is None


def test_handle_session_delete_files_all_deletes_completed_files(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)

    first = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="user_upload",
            data=b"first",
            filename="first.txt",
        )
    )
    second = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="user_upload",
            data=b"second",
            filename="second.txt",
        )
    )

    result = asyncio.run(
        file_service.handle_session_delete_files(
            "session-1",
            _user(),
            file_delete_mode="all",
        )
    )

    assert result["deletedFileIds"] == sorted([first["id"], second["id"]])
    assert result["archivedFileIds"] == []
    assert pool.rows == {}
    assert store.objects == {}


def test_global_files_enforce_member_owner_and_admin_tenant(monkeypatch):
    store = FakeStore()
    pool = FakePool()
    monkeypatch.setattr(file_service, "get_store", lambda: asyncio.sleep(0, store))
    monkeypatch.setattr(file_service, "get_pool", lambda: pool)

    own = asyncio.run(
        file_service.save_bytes(
            session_id="session-1",
            source="user_upload",
            data=b"own",
            filename="own.txt",
        )
    )
    other = asyncio.run(
        file_service.save_bytes(
            session_id="session-3",
            source="user_upload",
            data=b"other",
            filename="other.txt",
        )
    )

    member_files = asyncio.run(file_service.list_global_files(_user(role="member")))
    admin_files = asyncio.run(file_service.list_global_files(_user(role="admin")))

    assert [item["id"] for item in member_files] == [own["id"]]
    assert {item["id"] for item in admin_files} == {own["id"], other["id"]}
    with pytest.raises(file_service.HTTPException) as member_exc:
        asyncio.run(file_service.get_file_payload(other["id"], _user(role="member")))
    assert member_exc.value.status_code == 404


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
    assert renamed["url"].startswith(f"http://localhost:8000/api/files/{saved['id']}.txt?")
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
