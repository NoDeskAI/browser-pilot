from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, require_role
from app.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter()

_KEY = "storage_config"

_S3_REQUIRED_FIELDS = {
    "s3Bucket": "Bucket",
    "s3Region": "Region",
    "s3AccessKey": "Access Key",
    "s3SecretKey": "Secret Key",
}


class StorageConfig(BaseModel):
    storage: str = "builtin"
    s3Bucket: str = ""
    s3Region: str = ""
    s3AccessKey: str = ""
    s3SecretKey: str = ""
    s3Endpoint: str = ""
    s3Presign: bool = True
    s3PresignExpires: int = 3600


async def _verify_s3_connection(cfg: StorageConfig) -> None:
    """Try HeadBucket to confirm credentials & bucket are valid."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    session = boto3.Session(
        aws_access_key_id=cfg.s3AccessKey,
        aws_secret_access_key=cfg.s3SecretKey,
        region_name=cfg.s3Region,
    )
    kwargs = {"endpoint_url": cfg.s3Endpoint} if cfg.s3Endpoint else {}
    client = session.client("s3", **kwargs)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, lambda: client.head_bucket(Bucket=cfg.s3Bucket)
        )
    except ClientError as exc:
        code = exc.response["Error"].get("Code", "")
        if code == "403":
            raise HTTPException(422, detail="s3_forbidden")
        elif code == "404":
            raise HTTPException(422, detail="s3_bucket_not_found")
        else:
            logger.warning("S3 verify failed: %s", exc)
            raise HTTPException(422, detail="s3_connect_failed")
    except BotoCoreError as exc:
        logger.warning("S3 verify failed: %s", exc)
        raise HTTPException(422, detail="s3_connect_failed")
    except Exception as exc:
        logger.warning("S3 verify failed: %s", exc)
        raise HTTPException(422, detail="s3_connect_failed")


@router.get("/api/settings/storage")
async def get_storage_settings(_user: CurrentUser = Depends(require_role(["superadmin", "admin"]))):
    pool = get_pool()
    row = await pool.fetchrow("SELECT value FROM app_state WHERE key = $1", _KEY)
    if row:
        return StorageConfig(**json.loads(row["value"])).model_dump()
    return StorageConfig().model_dump()


@router.put("/api/settings/storage")
async def save_storage_settings(body: StorageConfig, _user: CurrentUser = Depends(require_role(["superadmin", "admin"]))):
    if body.storage == "s3":
        missing = [
            label
            for field, label in _S3_REQUIRED_FIELDS.items()
            if not getattr(body, field, "").strip()
        ]
        if missing:
            raise HTTPException(422, detail="s3_missing_fields")

        await _verify_s3_connection(body)

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


class OrganizationConfig(BaseModel):
    name: str


@router.get("/api/settings/organization")
async def get_organization(user: CurrentUser = Depends(require_role(["superadmin", "admin"]))):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, name, slug, created_at FROM tenants WHERE id = $1",
        user.tenant_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "createdAt": row["created_at"].isoformat(),
    }


@router.put("/api/settings/organization")
async def update_organization(
    body: OrganizationConfig, user: CurrentUser = Depends(require_role(["superadmin", "admin"]))
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name cannot be empty")

    pool = get_pool()
    await pool.execute(
        "UPDATE tenants SET name = $1 WHERE id = $2",
        name,
        user.tenant_id,
    )
    logger.info("Organization name updated to %s by user %s", name, user.id)
    return {"ok": True}
