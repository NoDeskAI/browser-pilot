from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter()


@router.get("/api/files/{file_id}.{ext}")
async def serve_file(file_id: str, ext: str):
    from app.file_store import BuiltinStore, get_store

    store = await get_store()
    if not isinstance(store, BuiltinStore):
        raise HTTPException(404)
    result = store.get(file_id)
    if not result:
        raise HTTPException(404, "File not found or expired")
    data, content_type = result
    return Response(content=data, media_type=content_type)
