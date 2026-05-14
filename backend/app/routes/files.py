from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_session_aware_user, verify_session_access

router = APIRouter()


class FileCaptureHeartbeat(BaseModel):
    status: str = "running"
    error: str = ""
    downloads: list[dict[str, Any]] | None = None


class RenameSessionFileBody(BaseModel):
    name: str


def _runtime_token(authorization: str) -> str:
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return ""


def _tmp_filename(name: str) -> str:
    safe = Path(str(name or "file")).name
    return safe if safe and safe not in {".", ".."} else "file"


@router.get("/api/files/{file_id}.{ext}")
async def serve_file(file_id: str, ext: str, user: CurrentUser = Depends(get_session_aware_user)):
    from app.file_service import get_file_payload
    from app.file_store import get_store

    result = await get_file_payload(file_id, user)
    if result is None and not user.session_scope:
        store = await get_store()
        result = await store.get(file_id)
    if not result:
        raise HTTPException(404, "File not found or expired")
    data, content_type = result
    return Response(content=data, media_type=content_type)


@router.post("/api/sessions/{session_id}/files/heartbeat")
async def heartbeat_file_capture_route(
    session_id: str,
    body: FileCaptureHeartbeat | None = None,
    authorization: str = Header("", alias="Authorization"),
):
    from app.file_capture import heartbeat_file_capture, verify_file_capture_token

    await verify_file_capture_token(session_id, _runtime_token(authorization))
    await heartbeat_file_capture(
        session_id,
        status=(body.status if body else "running"),
        error=(body.error if body else ""),
        downloads=(body.downloads if body else None),
    )
    return {"ok": True}


@router.post("/api/sessions/{session_id}/files/ingest")
async def ingest_session_file(
    session_id: str,
    file: UploadFile = File(...),
    source: str = Form("browser_download"),
    sourceId: str = Form(""),
    originalName: str = Form(""),
    contentType: str = Form(""),
    sizeBytes: int | None = Form(None),
    sourcePath: str = Form(""),
    sourceMtime: float | None = Form(None),
    sha256: str = Form(""),
    authorization: str = Header("", alias="Authorization"),
):
    from app.file_capture import clear_active_download, heartbeat_file_capture, verify_file_capture_token
    from app.file_service import save_file

    await verify_file_capture_token(session_id, _runtime_token(authorization))
    if source != "browser_download":
        raise HTTPException(422, "Unsupported file source")

    filename = originalName.strip() or file.filename or "download"
    declared_type = contentType.strip() or file.content_type or "application/octet-stream"
    expected_sha = sha256.strip().lower()

    with tempfile.TemporaryDirectory(prefix="bp-ingest-") as tmp:
        tmp_path = Path(tmp) / _tmp_filename(filename)
        digest = hashlib.sha256()
        total = 0
        with tmp_path.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
                fh.write(chunk)

        if sizeBytes is not None and total != sizeBytes:
            raise HTTPException(422, "Uploaded file size does not match sizeBytes")
        actual_sha = digest.hexdigest()
        if expected_sha and expected_sha != actual_sha:
            raise HTTPException(422, "Uploaded file sha256 does not match")

        saved = await save_file(
            session_id=session_id,
            source=source,
            path=tmp_path,
            filename=filename,
            content_type=declared_type,
            source_id=sourceId.strip() or None,
            source_path=sourcePath.strip() or None,
            source_mtime=sourceMtime,
            sha256=actual_sha,
        )

    clear_active_download(session_id, sourceId.strip() or None)
    await heartbeat_file_capture(session_id, status="running")
    return {"ok": True, "file": saved}


@router.post("/api/sessions/{session_id}/files")
async def upload_session_file(
    session_id: str,
    file: UploadFile = File(...),
    originalName: str = Form(""),
    user: CurrentUser = Depends(get_session_aware_user),
):
    from app.file_service import save_file

    await verify_session_access(session_id, user)
    filename = originalName.strip() or file.filename or "file"
    content_type = file.content_type or "application/octet-stream"

    with tempfile.TemporaryDirectory(prefix="bp-upload-") as tmp:
        tmp_path = Path(tmp) / _tmp_filename(filename)
        with tmp_path.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)

        saved = await save_file(
            session_id=session_id,
            source="user_upload",
            path=tmp_path,
            filename=filename,
            content_type=content_type,
        )

    return {"ok": True, "file": saved}


@router.get("/api/sessions/{session_id}/files/{file_id}")
async def get_session_file_route(
    session_id: str,
    file_id: str,
    user: CurrentUser = Depends(get_session_aware_user),
):
    from app.file_service import get_session_file

    await verify_session_access(session_id, user)
    return {"file": await get_session_file(session_id, file_id)}


@router.patch("/api/sessions/{session_id}/files/{file_id}")
async def rename_session_file_route(
    session_id: str,
    file_id: str,
    body: RenameSessionFileBody,
    user: CurrentUser = Depends(get_session_aware_user),
):
    from app.file_service import rename_session_file

    await verify_session_access(session_id, user)
    return {"ok": True, "file": await rename_session_file(session_id, file_id, body.name)}


@router.delete("/api/sessions/{session_id}/files/{file_id}")
async def delete_session_file_route(
    session_id: str,
    file_id: str,
    user: CurrentUser = Depends(get_session_aware_user),
):
    from app.file_service import delete_session_file

    await verify_session_access(session_id, user)
    return await delete_session_file(session_id, file_id)
