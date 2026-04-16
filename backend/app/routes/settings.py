from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, require_role
from app.db import get_pool

router = APIRouter()

_KEY = "storage_config"


class StorageConfig(BaseModel):
    storage: str = "builtin"
    s3Bucket: str = ""
    s3Region: str = ""
    s3AccessKey: str = ""
    s3SecretKey: str = ""
    s3Endpoint: str = ""
    s3Presign: bool = True
    s3PresignExpires: int = 3600


@router.get("/api/settings/storage")
async def get_storage_settings(_user: CurrentUser = Depends(require_role(["superadmin", "admin"]))):
    pool = get_pool()
    row = await pool.fetchrow("SELECT value FROM app_state WHERE key = $1", _KEY)
    if row:
        return StorageConfig(**json.loads(row["value"])).model_dump()
    return StorageConfig().model_dump()


@router.put("/api/settings/storage")
async def save_storage_settings(body: StorageConfig, _user: CurrentUser = Depends(require_role(["superadmin", "admin"]))):
    pool = get_pool()
    await pool.execute(
        """INSERT INTO app_state (key, value) VALUES ($1, $2)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
        _KEY,
        json.dumps(body.model_dump(), ensure_ascii=False),
    )
    from app.file_store import invalidate_store

    await invalidate_store()
    return {"ok": True}
