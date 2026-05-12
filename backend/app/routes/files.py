from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_session_aware_user

router = APIRouter()


class FileCaptureHeartbeat(BaseModel):
    status: str = "running"
    error: str = ""


def _runtime_token(authorization: str) -> str:
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return ""


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
    from app.file_capture import heartbeat_file_capture, verify_file_capture_token
    from app.file_service import save_file

    await verify_file_capture_token(session_id, _runtime_token(authorization))
    if source != "browser_download":
        raise HTTPException(422, "Unsupported file source")

    filename = originalName.strip() or file.filename or "download"
    declared_type = contentType.strip() or file.content_type or "application/octet-stream"
    expected_sha = sha256.strip().lower()

    with tempfile.TemporaryDirectory(prefix="bp-ingest-") as tmp:
        tmp_path = Path(tmp) / Path(filename).name
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

    await heartbeat_file_capture(session_id, status="running")
    return {"ok": True, "file": saved}
