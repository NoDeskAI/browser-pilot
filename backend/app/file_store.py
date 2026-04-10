from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Protocol

logger = logging.getLogger("file_store")


class FileStore(Protocol):
    async def save(
        self,
        b64_data: str,
        session_id: str,
        content_type: str = "image/png",
        ext: str = "png",
    ) -> dict: ...


class S3Store:
    def __init__(
        self,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint: str,
        presign: bool,
        presign_expires: int,
    ):
        import boto3

        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        kwargs = {"endpoint_url": endpoint} if endpoint else {}
        self._client = session.client("s3", **kwargs)
        self._bucket = bucket
        self._presign = presign
        self._presign_expires = presign_expires

    async def save(
        self,
        b64_data: str,
        session_id: str,
        content_type: str = "image/png",
        ext: str = "png",
    ) -> dict:
        raw = base64.b64decode(b64_data)
        key = f"files/{session_id}/{uuid.uuid4().hex[:12]}.{ext}"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.put_object(
                Bucket=self._bucket, Key=key, Body=raw, ContentType=content_type
            ),
        )
        if self._presign:
            url = await loop.run_in_executor(
                None,
                lambda: self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": key},
                    ExpiresIn=self._presign_expires,
                ),
            )
        else:
            ep = self._client.meta.endpoint_url
            url = (
                f"{ep}/{self._bucket}/{key}"
                if ep
                else f"https://{self._bucket}.s3.amazonaws.com/{key}"
            )
        return {"type": "url", "url": url}


class BuiltinStore:
    def __init__(self, base_url: str, ttl: int = 3600):
        self._cache: dict[str, tuple[bytes, str, float]] = {}
        self._base_url = base_url.rstrip("/")
        self._ttl = ttl

    async def save(
        self,
        b64_data: str,
        session_id: str,
        content_type: str = "image/png",
        ext: str = "png",
    ) -> dict:
        raw = base64.b64decode(b64_data)
        fid = uuid.uuid4().hex[:16]
        self._cache[fid] = (raw, content_type, time.time())
        self._cleanup()
        return {"type": "url", "url": f"{self._base_url}/api/files/{fid}.{ext}"}

    def get(self, file_id: str) -> tuple[bytes, str] | None:
        item = self._cache.get(file_id)
        if item and time.time() - item[2] < self._ttl:
            return item[0], item[1]
        self._cache.pop(file_id, None)
        return None

    def _cleanup(self):
        now = time.time()
        expired = [k for k, v in self._cache.items() if now - v[2] > self._ttl]
        for k in expired:
            del self._cache[k]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_store: FileStore | None = None


async def get_store() -> FileStore:
    global _store
    if _store is None:
        await _init_store()
    return _store  # type: ignore[return-value]


async def _init_store():
    global _store
    config = await _load_config_from_db()
    mode = config.get("storage", "builtin")

    if mode == "s3" and config.get("s3Bucket"):
        _store = S3Store(
            bucket=config["s3Bucket"],
            region=config["s3Region"],
            access_key=config["s3AccessKey"],
            secret_key=config["s3SecretKey"],
            endpoint=config.get("s3Endpoint", ""),
            presign=config.get("s3Presign", True),
            presign_expires=config.get("s3PresignExpires", 3600),
        )
    else:
        from app.config import API_BASE_URL

        _store = BuiltinStore(API_BASE_URL, ttl=3600)


async def _load_config_from_db() -> dict:
    from app.db import get_pool

    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT value FROM app_state WHERE key = 'storage_config'"
    )
    return json.loads(row["value"]) if row else {}


async def invalidate_store():
    global _store
    _store = None
