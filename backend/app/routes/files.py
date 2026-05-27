from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app import agent_devices
from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    get_optional_session_aware_user,
    get_session_aware_user,
    verify_session_access,
)

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
async def serve_file(
    file_id: str,
    ext: str,
    expires: int | None = Query(None),
    signature: str | None = Query(None),
    user: CurrentUser | None = Depends(get_optional_session_aware_user),
):
    from app.file_service import get_file_payload, get_signed_file_payload
    from app.file_urls import verify_file_download_signature
    from app.file_store import get_store

    result = None
    if user:
        result = await get_file_payload(file_id, user)
        if result is None and not user.session_scope:
            store = await get_store()
            result = await store.get(file_id)
    else:
        if expires is None or not signature:
            raise HTTPException(401, "Not authenticated")
        if not verify_file_download_signature(file_id, ext, expires, signature):
            raise HTTPException(403, "File URL expired or invalid")
        result = await get_signed_file_payload(file_id)
    if not result:
        raise HTTPException(404, "File not found or expired")
    data, content_type = result
    return Response(content=data, media_type=content_type)


@router.get("/api/files")
async def list_files_route(user: CurrentUser = Depends(get_current_user)):
    from app.file_service import list_global_files

    return {"files": await list_global_files(user)}


@router.patch("/api/files/{file_id}")
async def rename_file_route(
    file_id: str,
    body: RenameSessionFileBody,
    user: CurrentUser = Depends(get_current_user),
):
    from app.file_service import rename_global_file

    return {"ok": True, "file": await rename_global_file(file_id, user, body.name)}


@router.delete("/api/files/{file_id}")
async def delete_file_route(file_id: str, user: CurrentUser = Depends(get_current_user)):
    from app.file_service import delete_global_file

    return await delete_global_file(file_id, user)


@router.post("/api/sessions/{session_id}/files/heartbeat")
async def heartbeat_file_capture_route(
    session_id: str,
    body: FileCaptureHeartbeat | None = None,
    authorization: str = Header("", alias="Authorization"),
):
    from app.file_capture import heartbeat_file_capture, verify_file_capture_token

    try:
        await verify_file_capture_token(session_id, _runtime_token(authorization))
    except HTTPException as exc:
        await agent_devices.record_runtime_action(
            session_id,
            action="session.files.heartbeat",
            outcome="rejected",
            side_effect_level="none",
            summary="Runtime file capture heartbeat rejected by token validation",
            details={"statusCode": exc.status_code},
            error="invalid_runtime_token",
        )
        raise
    try:
        await heartbeat_file_capture(
            session_id,
            status=(body.status if body else "running"),
            error=(body.error if body else ""),
            downloads=(body.downloads if body else None),
        )
        await agent_devices.record_runtime_action(
            session_id,
            action="session.files.heartbeat",
            outcome="succeeded",
            side_effect_level="internal",
            summary="Runtime file capture heartbeat received",
            details={
                "status": body.status if body else "running",
                "downloadCount": len(body.downloads or []) if body else 0,
            },
        )
        return {"ok": True}
    except Exception as exc:
        await agent_devices.record_runtime_action(
            session_id,
            action="session.files.heartbeat",
            outcome="failed",
            side_effect_level="internal",
            summary="Runtime file capture heartbeat failed",
            error=str(exc),
        )
        raise


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

    try:
        await verify_file_capture_token(session_id, _runtime_token(authorization))
    except HTTPException as exc:
        await agent_devices.record_runtime_action(
            session_id,
            action="session.files.ingest",
            outcome="rejected",
            side_effect_level="none",
            summary="Runtime file capture ingest rejected by token validation",
            details={"statusCode": exc.status_code},
            error="invalid_runtime_token",
        )
        raise
    filename = originalName.strip() or file.filename or "download"
    try:
        if source != "browser_download":
            raise HTTPException(422, "Unsupported file source")

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
        await agent_devices.record_runtime_action(
            session_id,
            action="session.files.ingest",
            outcome="succeeded",
            side_effect_level="internal",
            summary=f"Runtime ingested browser download {filename}",
            evidence_refs=[{"type": "session_file", "id": saved.get("id"), "url": saved.get("url")}],
            details={"sourceId": sourceId.strip() or None, "sizeBytes": total},
        )
        return {"ok": True, "file": saved}
    except Exception as exc:
        await agent_devices.record_runtime_action(
            session_id,
            action="session.files.ingest",
            outcome="failed",
            side_effect_level="internal",
            summary=f"Runtime failed to ingest browser download {filename}",
            details={"sourceId": sourceId.strip() or None},
            error=str(exc),
        )
        raise


@router.post("/api/sessions/{session_id}/files")
async def upload_session_file(
    session_id: str,
    file: UploadFile = File(...),
    originalName: str = Form(""),
    user: CurrentUser = Depends(get_session_aware_user),
):
    from app.file_service import save_file

    await verify_session_access(session_id, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        session_id, user, action="session.files.upload", side_effect_level="internal"
    )
    if rejected:
        return rejected
    filename = originalName.strip() or file.filename or "file"
    content_type = file.content_type or "application/octet-stream"
    try:
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

        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "file": saved},
            summary=f"Uploaded file {filename} into session",
            evidence_refs=[{"type": "session_file", "id": saved.get("id"), "url": saved.get("url")}],
            details={"filename": filename, "contentType": content_type},
            retry_safety="unknown",
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc))


@router.get("/api/sessions/{session_id}/files/{file_id}")
async def get_session_file_route(
    session_id: str,
    file_id: str,
    user: CurrentUser = Depends(get_session_aware_user),
):
    from app.file_service import get_session_file

    await verify_session_access(session_id, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        session_id, user, action="session.files.get", side_effect_level="none"
    )
    if rejected:
        return rejected
    try:
        file_payload = await get_session_file(session_id, file_id)
        return await agent_devices.complete_compatible_action(
            ctx,
            {"file": file_payload},
            summary=f"Read session file {file_id}",
            evidence_refs=[{"type": "session_file", "id": file_payload.get("id"), "url": file_payload.get("url")}],
            retry_safety="safe",
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc), retry_safety="safe")


@router.patch("/api/sessions/{session_id}/files/{file_id}")
async def rename_session_file_route(
    session_id: str,
    file_id: str,
    body: RenameSessionFileBody,
    user: CurrentUser = Depends(get_session_aware_user),
):
    from app.file_service import rename_session_file

    await verify_session_access(session_id, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        session_id, user, action="session.files.rename", side_effect_level="internal"
    )
    if rejected:
        return rejected
    try:
        renamed = await rename_session_file(session_id, file_id, body.name)
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "file": renamed},
            summary=f"Renamed session file {file_id}",
            evidence_refs=[{"type": "session_file", "id": renamed.get("id"), "url": renamed.get("url")}],
            details={"name": body.name},
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc))


@router.delete("/api/sessions/{session_id}/files/{file_id}")
async def delete_session_file_route(
    session_id: str,
    file_id: str,
    user: CurrentUser = Depends(get_session_aware_user),
):
    from app.file_service import delete_session_file

    await verify_session_access(session_id, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        session_id, user, action="session.files.delete", side_effect_level="internal"
    )
    if rejected:
        return rejected
    try:
        result = await delete_session_file(session_id, file_id)
        return await agent_devices.complete_compatible_action(
            ctx,
            result,
            summary=f"Deleted session file {file_id}",
            evidence_refs=[{"type": "session_file", "id": file_id}],
            details=result,
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc))
