from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.auth.dependencies import CurrentUser
from app.config import API_BASE_URL
from app.db import get_pool
from app.file_store import get_store


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


def _file_url(file_id: str, filename: str) -> str:
    suffix = Path(filename).suffix or ".bin"
    return f"{API_BASE_URL.rstrip()}/api/files/{file_id}{suffix}"


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _file_dto(row: Any) -> dict[str, Any]:
    name = row["original_name"]
    uploaded_at = _row_value(row, "uploaded_at")
    return {
        "id": row["id"],
        "name": name,
        "source": row["source"],
        "sourceId": _row_value(row, "source_id"),
        "contentType": row["content_type"],
        "size": row["size_bytes"],
        "url": _file_url(row["id"], name),
        "storage": row["storage"],
        "sha256": _row_value(row, "sha256"),
        "uploadedAt": uploaded_at.isoformat() if uploaded_at else None,
        "createdAt": row["created_at"].isoformat() if row["created_at"] else "",
    }


async def _session_tenant_id(session_id: str) -> str | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT tenant_id FROM sessions WHERE id = $1", session_id)
    if not row:
        raise HTTPException(404, "Session not found")
    return row["tenant_id"]


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
            return _file_dto(row)
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
    return _file_dto(row) if row else None


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

    tenant_id = await _session_tenant_id(session_id)
    file_id = uuid.uuid4().hex
    object_key = f"files/{session_id}/{file_id}/{filename}"
    store = await get_store()
    await store.save_bytes(data, key=object_key, content_type=content_type)
    return await _insert_file_record(
        file_id=file_id,
        session_id=session_id,
        tenant_id=tenant_id,
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

    tenant_id = await _session_tenant_id(session_id)
    file_id = uuid.uuid4().hex
    object_key = f"files/{session_id}/{file_id}/{filename}"
    store = await get_store()
    await store.save_file(path, key=object_key, content_type=content_type)
    return await _insert_file_record(
        file_id=file_id,
        session_id=session_id,
        tenant_id=tenant_id,
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
            id, session_id, tenant_id, source, original_name, content_type,
            size_bytes, storage, object_key, source_id, source_path, source_mtime, sha256, uploaded_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW())
        ON CONFLICT DO NOTHING
        """,
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
    )
    row = await pool.fetchrow("SELECT * FROM session_files WHERE id = $1", file_id)
    if row:
        return _file_dto(row)
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
    return [_file_dto(row) for row in rows]


async def get_file_payload(file_id: str, user: CurrentUser) -> tuple[bytes, str] | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM session_files WHERE id = $1", file_id)
    if not row:
        return None
    if user.session_scope:
        if row["session_id"] != user.session_scope:
            raise HTTPException(403, "Token not authorized for this file")
    elif row["tenant_id"] and row["tenant_id"] != user.tenant_id:
        raise HTTPException(404, "File not found")

    store = await get_store()
    return await store.get_by_key(row["object_key"])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
