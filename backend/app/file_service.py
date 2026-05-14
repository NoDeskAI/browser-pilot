from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.auth.dependencies import CurrentUser
from app.db import get_pool
from app.file_urls import FILE_DOWNLOAD_URL_TTL_SECONDS, backend_download_url
from app.file_store import get_store

logger = logging.getLogger("file_service")


def _safe_filename(name: str, default: str = "file") -> str:
    raw = Path(str(name or "")).name.strip()
    if not raw:
        raw = default
    raw = re.sub(r"[\x00-\x1f/\\:]+", "_", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" .")
    return raw or default


def _content_type(filename: str, fallback: str = "application/octet-stream") -> str:
    guessed, _encoding = mimetypes.guess_type(filename)
    return guessed or fallback


async def _file_url(row: Any) -> str:
    name = row["original_name"]
    file_id = row["id"]
    object_key = _row_value(row, "object_key")
    if _row_value(row, "storage") == "s3" and object_key:
        store = await get_store()
        download_url = getattr(store, "download_url", None)
        if getattr(store, "storage_name", "") == "s3" and download_url:
            return await download_url(
                key=object_key,
                file_id=file_id,
                filename=name,
                expires_in=FILE_DOWNLOAD_URL_TTL_SECONDS,
            )
    return backend_download_url(
        file_id,
        name,
        expires_in=FILE_DOWNLOAD_URL_TTL_SECONDS,
    )


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


async def _file_dto(row: Any) -> dict[str, Any]:
    name = row["original_name"]
    uploaded_at = _row_value(row, "uploaded_at")
    archived_at = _row_value(row, "archived_at")
    return {
        "id": row["id"],
        "sessionId": _row_value(row, "session_id"),
        "archivedSessionId": _row_value(row, "archived_session_id"),
        "archivedSessionName": _row_value(row, "archived_session_name"),
        "name": name,
        "status": "completed",
        "source": row["source"],
        "sourceId": _row_value(row, "source_id"),
        "contentType": row["content_type"],
        "size": row["size_bytes"],
        "url": await _file_url(row),
        "storage": row["storage"],
        "sha256": _row_value(row, "sha256"),
        "uploadedAt": uploaded_at.isoformat() if uploaded_at else None,
        "archivedAt": archived_at.isoformat() if archived_at else None,
        "createdAt": row["created_at"].isoformat() if row["created_at"] else "",
    }


async def _session_context(session_id: str) -> Any:
    pool = get_pool()
    row = await pool.fetchrow("SELECT tenant_id, user_id, name FROM sessions WHERE id = $1", session_id)
    if not row:
        raise HTTPException(404, "Session not found")
    return row


def _assert_file_access(row: Any, user: CurrentUser) -> None:
    if user.session_scope:
        if _row_value(row, "session_id") != user.session_scope:
            raise HTTPException(403, "Token not authorized for this file")
        return

    tenant_id = _row_value(row, "tenant_id")
    if tenant_id and tenant_id != user.tenant_id:
        raise HTTPException(404, "File not found")
    if user.role == "member" and _row_value(row, "user_id") != user.id:
        raise HTTPException(404, "File not found")


def _assert_global_file_access(row: Any, user: CurrentUser) -> None:
    if user.session_scope:
        raise HTTPException(403, "Session-scoped tokens cannot access this endpoint")
    _assert_file_access(row, user)


async def _existing_download(
    *,
    session_id: str,
    source: str,
    source_path: str | None,
    source_mtime: float | None,
    size_bytes: int,
    source_id: str | None = None,
) -> dict[str, Any] | None:
    pool = get_pool()
    if source_id:
        row = await pool.fetchrow(
            """
            SELECT * FROM session_files
            WHERE session_id = $1
              AND source = $2
              AND source_id = $3
            ORDER BY created_at DESC
            LIMIT 1
            """,
            session_id,
            source,
            source_id,
        )
        if row:
            return await _file_dto(row)
    if not source_path or source_mtime is None:
        return None
    row = await pool.fetchrow(
        """
        SELECT * FROM session_files
        WHERE session_id = $1
          AND source = $2
          AND source_path = $3
          AND source_mtime = $4
          AND size_bytes = $5
        ORDER BY created_at DESC
        LIMIT 1
        """,
        session_id,
        source,
        source_path,
        source_mtime,
        size_bytes,
    )
    return await _file_dto(row) if row else None


async def save_bytes(
    *,
    session_id: str,
    source: str,
    data: bytes,
    filename: str,
    content_type: str | None = None,
    source_id: str | None = None,
    source_path: str | None = None,
    source_mtime: float | None = None,
    sha256: str | None = None,
) -> dict[str, Any]:
    filename = _safe_filename(filename, "file")
    content_type = content_type or _content_type(filename)
    size_bytes = len(data)
    sha256 = sha256 or hashlib.sha256(data).hexdigest()
    existing = await _existing_download(
        session_id=session_id,
        source=source,
        source_id=source_id,
        source_path=source_path,
        source_mtime=source_mtime,
        size_bytes=size_bytes,
    )
    if existing:
        return existing

    session = await _session_context(session_id)
    file_id = uuid.uuid4().hex
    object_key = f"files/{session_id}/{file_id}/{filename}"
    store = await get_store()
    await store.save_bytes(data, key=object_key, content_type=content_type)
    return await _insert_file_record(
        file_id=file_id,
        session_id=session_id,
        tenant_id=_row_value(session, "tenant_id"),
        user_id=_row_value(session, "user_id"),
        source=source,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage=store.storage_name,
        object_key=object_key,
        source_id=source_id,
        source_path=source_path,
        source_mtime=source_mtime,
        sha256=sha256,
    )


async def save_file(
    *,
    session_id: str,
    source: str,
    path: Path,
    filename: str | None = None,
    content_type: str | None = None,
    source_id: str | None = None,
    source_path: str | None = None,
    source_mtime: float | None = None,
    sha256: str | None = None,
) -> dict[str, Any]:
    filename = _safe_filename(filename or path.name, "file")
    content_type = content_type or _content_type(filename)
    size_bytes = path.stat().st_size
    sha256 = sha256 or _sha256_file(path)
    existing = await _existing_download(
        session_id=session_id,
        source=source,
        source_id=source_id,
        source_path=source_path,
        source_mtime=source_mtime,
        size_bytes=size_bytes,
    )
    if existing:
        return existing

    session = await _session_context(session_id)
    file_id = uuid.uuid4().hex
    object_key = f"files/{session_id}/{file_id}/{filename}"
    store = await get_store()
    await store.save_file(path, key=object_key, content_type=content_type)
    return await _insert_file_record(
        file_id=file_id,
        session_id=session_id,
        tenant_id=_row_value(session, "tenant_id"),
        user_id=_row_value(session, "user_id"),
        source=source,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage=store.storage_name,
        object_key=object_key,
        source_id=source_id,
        source_path=source_path,
        source_mtime=source_mtime,
        sha256=sha256,
    )


async def _insert_file_record(
    *,
    file_id: str,
    session_id: str,
    tenant_id: str | None,
    user_id: str | None,
    source: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    storage: str,
    object_key: str,
    source_id: str | None,
    source_path: str | None,
    source_mtime: float | None,
    sha256: str | None,
) -> dict[str, Any]:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO session_files (
            id, session_id, tenant_id, user_id, source, original_name, content_type,
            size_bytes, storage, object_key, source_id, source_path, source_mtime, sha256, uploaded_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, NOW())
        ON CONFLICT DO NOTHING
        """,
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
    )
    row = await pool.fetchrow("SELECT * FROM session_files WHERE id = $1", file_id)
    if row:
        return await _file_dto(row)
    existing = await _existing_download(
        session_id=session_id,
        source=source,
        source_id=source_id,
        source_path=source_path,
        source_mtime=source_mtime,
        size_bytes=size_bytes,
    )
    if existing:
        return existing
    raise RuntimeError("Failed to create file record")


async def list_session_files(session_id: str) -> list[dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM session_files
        WHERE session_id = $1
        ORDER BY created_at DESC
        """,
        session_id,
    )
    completed = [await _file_dto(row) for row in rows]
    completed_source_ids = {item.get("sourceId") for item in completed if item.get("sourceId")}

    from app.file_capture import list_active_downloads

    downloading = [
        item
        for item in list_active_downloads(session_id)
        if item.get("id") not in completed_source_ids
    ]
    return [*downloading, *completed]


async def get_session_file(session_id: str, file_id: str) -> dict[str, Any]:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM session_files WHERE session_id = $1 AND id = $2",
        session_id,
        file_id,
    )
    if not row:
        raise HTTPException(404, "File not found")
    return await _file_dto(row)


async def rename_session_file(session_id: str, file_id: str, name: str) -> dict[str, Any]:
    filename = _safe_filename(name, "file")
    pool = get_pool()
    row = await pool.fetchrow(
        """
        UPDATE session_files
        SET original_name = $3
        WHERE session_id = $1 AND id = $2
        RETURNING *
        """,
        session_id,
        file_id,
        filename,
    )
    if not row:
        raise HTTPException(404, "File not found")
    return await _file_dto(row)


async def delete_session_file(session_id: str, file_id: str) -> dict[str, Any]:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM session_files WHERE session_id = $1 AND id = $2",
        session_id,
        file_id,
    )
    if not row:
        raise HTTPException(404, "File not found")

    return await _delete_file_row(
        row,
        delete_sql="DELETE FROM session_files WHERE session_id = $1 AND id = $2",
        delete_args=(session_id, file_id),
        log_context=f"session={session_id} file={file_id}",
    )


async def _delete_file_row(
    row: Any,
    *,
    delete_sql: str,
    delete_args: tuple[Any, ...],
    log_context: str,
) -> dict[str, Any]:
    warning: str | None = None
    object_deleted = True
    store = await get_store()
    try:
        await store.delete_by_key(row["object_key"])
    except Exception as exc:
        object_deleted = False
        warning = "file_object_delete_failed"
        logger.warning(
            "File object delete failed; removing DB record only %s key=%s: %s",
            log_context,
            row["object_key"],
            exc,
        )

    pool = get_pool()
    result = await pool.execute(delete_sql, *delete_args)
    if result != "DELETE 1":
        logger.error("File row delete failed %s result=%s", log_context, result)
        raise HTTPException(500, "Failed to delete file record")

    return {
        "ok": True,
        "objectDeleted": object_deleted,
        "recordDeleted": True,
        "warning": warning,
    }


async def list_global_files(user: CurrentUser) -> list[dict[str, Any]]:
    if user.session_scope:
        raise HTTPException(403, "Session-scoped tokens cannot access this endpoint")
    pool = get_pool()
    if user.role in ("superadmin", "admin"):
        rows = await pool.fetch(
            """
            SELECT * FROM session_files
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            """,
            user.tenant_id,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM session_files
            WHERE tenant_id = $1 AND user_id = $2
            ORDER BY created_at DESC
            """,
            user.tenant_id,
            user.id,
        )
    return [await _file_dto(row) for row in rows]


async def get_global_file(file_id: str, user: CurrentUser) -> dict[str, Any]:
    row = await get_pool().fetchrow("SELECT * FROM session_files WHERE id = $1", file_id)
    if not row:
        raise HTTPException(404, "File not found")
    _assert_global_file_access(row, user)
    return await _file_dto(row)


async def rename_global_file(file_id: str, user: CurrentUser, name: str) -> dict[str, Any]:
    await get_global_file(file_id, user)
    filename = _safe_filename(name, "file")
    row = await get_pool().fetchrow(
        """
        UPDATE session_files
        SET original_name = $2
        WHERE id = $1
        RETURNING *
        """,
        file_id,
        filename,
    )
    if not row:
        raise HTTPException(404, "File not found")
    _assert_global_file_access(row, user)
    return await _file_dto(row)


async def delete_global_file(file_id: str, user: CurrentUser) -> dict[str, Any]:
    row = await get_pool().fetchrow("SELECT * FROM session_files WHERE id = $1", file_id)
    if not row:
        raise HTTPException(404, "File not found")
    _assert_global_file_access(row, user)
    return await _delete_file_row(
        row,
        delete_sql="DELETE FROM session_files WHERE id = $1",
        delete_args=(file_id,),
        log_context=f"file={file_id}",
    )


async def handle_session_delete_files(
    session_id: str,
    user: CurrentUser,
    *,
    file_delete_mode: str = "none",
    delete_file_ids: list[str] | None = None,
) -> dict[str, Any]:
    if user.session_scope:
        raise HTTPException(403, "Session-scoped tokens cannot delete sessions")
    session = await _session_context(session_id)
    if _row_value(session, "tenant_id") and _row_value(session, "tenant_id") != user.tenant_id:
        raise HTTPException(404, "Session not found")
    if user.role == "member" and _row_value(session, "user_id") != user.id:
        raise HTTPException(404, "Session not found")

    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM session_files
        WHERE session_id = $1
        ORDER BY created_at DESC
        """,
        session_id,
    )
    row_by_id = {row["id"]: row for row in rows}
    requested_ids = {str(file_id) for file_id in (delete_file_ids or []) if str(file_id).strip()}
    if file_delete_mode == "all":
        delete_ids = set(row_by_id)
    elif file_delete_mode == "selected":
        unknown_ids = requested_ids - set(row_by_id)
        if unknown_ids:
            raise HTTPException(422, "deleteFileIds contains files not owned by this session")
        delete_ids = requested_ids
    elif file_delete_mode == "none":
        delete_ids = set()
    else:
        raise HTTPException(422, "Unsupported fileDeleteMode")

    deleted_file_ids: list[str] = []
    object_delete_failed_file_ids: list[str] = []
    for file_id in delete_ids:
        result = await _delete_file_row(
            row_by_id[file_id],
            delete_sql="DELETE FROM session_files WHERE id = $1",
            delete_args=(file_id,),
            log_context=f"session={session_id} file={file_id}",
        )
        deleted_file_ids.append(file_id)
        if result.get("warning") == "file_object_delete_failed":
            object_delete_failed_file_ids.append(file_id)

    archive_ids = [file_id for file_id in row_by_id if file_id not in delete_ids]
    if archive_ids:
        await pool.execute(
            """
            UPDATE session_files
            SET session_id = NULL,
                archived_at = NOW(),
                archived_session_id = $1,
                archived_session_name = $2,
                tenant_id = COALESCE(tenant_id, $3),
                user_id = COALESCE(user_id, $4)
            WHERE session_id = $1 AND id = ANY($5::text[])
            """,
            session_id,
            _row_value(session, "name") or session_id,
            _row_value(session, "tenant_id"),
            _row_value(session, "user_id") or user.id,
            archive_ids,
        )

    return {
        "mode": file_delete_mode,
        "completedFileCount": len(rows),
        "deletedFileIds": sorted(deleted_file_ids),
        "archivedFileIds": archive_ids,
        "objectDeleteFailedFileIds": sorted(object_delete_failed_file_ids),
        "warning": "file_object_delete_failed" if object_delete_failed_file_ids else None,
    }


async def get_file_payload(file_id: str, user: CurrentUser) -> tuple[bytes, str] | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM session_files WHERE id = $1", file_id)
    if not row:
        return None
    _assert_file_access(row, user)

    store = await get_store()
    return await store.get_by_key(row["object_key"])


async def get_signed_file_payload(file_id: str) -> tuple[bytes, str] | None:
    row = await get_pool().fetchrow("SELECT * FROM session_files WHERE id = $1", file_id)
    if not row:
        return None
    store = await get_store()
    return await store.get_by_key(row["object_key"])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
