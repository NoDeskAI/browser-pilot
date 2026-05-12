from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.auth.dependencies import CurrentUser, get_session_aware_user

router = APIRouter()


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
