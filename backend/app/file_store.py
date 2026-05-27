from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Protocol

from app.file_urls import (
    FILE_DOWNLOAD_URL_TTL_SECONDS,
    attachment_content_disposition,
)

logger = logging.getLogger("file_store")


class FileStore(Protocol):
    storage_name: str

    async def save_bytes(
        self,
        data: bytes,
        *,
        key: str,
        content_type: str,
    ) -> None: ...

    async def save_file(
        self,
        path: Path,
        *,
        key: str,
        content_type: str,
    ) -> None: ...

    async def save(
        self,
        b64_data: str,
        session_id: str,
        content_type: str = "image/png",
        ext: str = "png",
    ) -> dict: ...

    async def get(self, file_id: str) -> tuple[bytes, str] | None: ...

    async def get_by_key(self, key: str) -> tuple[bytes, str] | None: ...

    async def delete_by_key(self, key: str) -> None: ...

    async def download_url(
        self,
        *,
        key: str,
        file_id: str,
        filename: str,
        expires_in: int = FILE_DOWNLOAD_URL_TTL_SECONDS,
    ) -> str: ...


def _encode_file_id(key: str) -> str:
    return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


def _decode_file_id(file_id: str) -> str | None:
    try:
        padding = "=" * (-len(file_id) % 4)
        return base64.urlsafe_b64decode(f"{file_id}{padding}").decode()
    except (binascii.Error, UnicodeDecodeError):
        return None


class S3Store:
    def __init__(
        self,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint: str,
        public_endpoint: str,
        presign: bool,
        presign_expires: int,
        base_url: str,
    ):
        import boto3
        from botocore.config import Config

        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        kwargs = {
            "config": Config(s3={"addressing_style": "path"}),
            **({"endpoint_url": endpoint} if endpoint else {}),
        }
        self._client = session.client("s3", **kwargs)
        public_endpoint = (public_endpoint or endpoint or "").strip()
        public_kwargs = {
            "config": Config(s3={"addressing_style": "path"}),
            **({"endpoint_url": public_endpoint} if public_endpoint else {}),
        }
        self._presign_client = session.client("s3", **public_kwargs)
        self._bucket = bucket
        self._presign = presign
        self._presign_expires = presign_expires
        self._base_url = base_url.rstrip("/")
        self.storage_name = "s3"

    async def save_bytes(
        self,
        data: bytes,
        *,
        key: str,
        content_type: str,
    ) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            ),
        )

    async def save_file(
        self,
        path: Path,
        *,
        key: str,
        content_type: str,
    ) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.upload_file(
                str(path), self._bucket, key, ExtraArgs={"ContentType": content_type}
            ),
        )

    async def save(
        self,
        b64_data: str,
        session_id: str,
        content_type: str = "image/png",
        ext: str = "png",
    ) -> dict:
        raw = base64.b64decode(b64_data)
        key = f"files/{session_id}/{uuid.uuid4().hex[:12]}.{ext}"
        await self.save_bytes(raw, key=key, content_type=content_type)
        file_id = _encode_file_id(key)
        url = f"{self._base_url}/api/files/{file_id}.{ext}"
        return {"type": "url", "url": url}

    async def get(self, file_id: str) -> tuple[bytes, str] | None:
        key = _decode_file_id(file_id)
        if not key:
            return None
        return await self.get_by_key(key)

    async def get_by_key(self, key: str) -> tuple[bytes, str] | None:
        loop = asyncio.get_running_loop()
        try:
            def fetch():
                obj = self._client.get_object(Bucket=self._bucket, Key=key)
                return obj["Body"].read(), obj.get("ContentType") or "application/octet-stream"

            return await loop.run_in_executor(None, fetch)
        except Exception as exc:
            logger.warning("S3 file fetch failed key=%s: %s", key, exc)
            return None

    async def delete_by_key(self, key: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.delete_object(Bucket=self._bucket, Key=key),
        )

    async def download_url(
        self,
        *,
        key: str,
        file_id: str,
        filename: str,
        expires_in: int = FILE_DOWNLOAD_URL_TTL_SECONDS,
    ) -> str:
        loop = asyncio.get_running_loop()

        def presign():
            return self._presign_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ResponseContentDisposition": attachment_content_disposition(filename),
                },
                ExpiresIn=expires_in,
            )

        return await loop.run_in_executor(None, presign)


class BuiltinStore:
    def __init__(self, base_url: str, ttl: int = 3600):
        self._cache: dict[str, tuple[bytes, str, float]] = {}
        self._base_url = base_url.rstrip("/")
        self._ttl = ttl
        self.storage_name = "builtin"

    async def save_bytes(
        self,
        data: bytes,
        *,
        key: str,
        content_type: str,
    ) -> None:
        self._cache[key] = (data, content_type, time.time())
        self._cleanup()

    async def save_file(
        self,
        path: Path,
        *,
        key: str,
        content_type: str,
    ) -> None:
        self._cache[key] = (path.read_bytes(), content_type, time.time())
        self._cleanup()

    async def save(
        self,
        b64_data: str,
        session_id: str,
        content_type: str = "image/png",
        ext: str = "png",
    ) -> dict:
        raw = base64.b64decode(b64_data)
        key = f"files/{session_id}/{uuid.uuid4().hex[:12]}.{ext}"
        await self.save_bytes(raw, key=key, content_type=content_type)
        fid = _encode_file_id(key)
        return {"type": "url", "url": f"{self._base_url}/api/files/{fid}.{ext}"}

    async def get(self, file_id: str) -> tuple[bytes, str] | None:
        key = _decode_file_id(file_id) or file_id
        return await self.get_by_key(key)

    async def get_by_key(self, key: str) -> tuple[bytes, str] | None:
        item = self._cache.get(key)
        if item and time.time() - item[2] < self._ttl:
            return item[0], item[1]
        self._cache.pop(key, None)
        return None

    async def delete_by_key(self, key: str) -> None:
        self._cache.pop(key, None)

    async def download_url(
        self,
        *,
        key: str,
        file_id: str,
        filename: str,
        expires_in: int = FILE_DOWNLOAD_URL_TTL_SECONDS,
    ) -> str:
        from app.file_urls import backend_download_url

        return backend_download_url(file_id, filename, expires_in=expires_in)

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
    from app.config import API_BASE_URL, BUNDLED_S3_ENDPOINT, BUNDLED_S3_PUBLIC_ENDPOINT

    if mode == "s3" and config.get("s3Bucket"):
        public_endpoint = config.get("s3PublicEndpoint", "")
        if not public_endpoint and config.get("s3Endpoint", "") == BUNDLED_S3_ENDPOINT:
            public_endpoint = BUNDLED_S3_PUBLIC_ENDPOINT
        _store = S3Store(
            bucket=config["s3Bucket"],
            region=config["s3Region"],
            access_key=config["s3AccessKey"],
            secret_key=config["s3SecretKey"],
            endpoint=config.get("s3Endpoint", ""),
            public_endpoint=public_endpoint,
            presign=config.get("s3Presign", True),
            presign_expires=config.get("s3PresignExpires", 3600),
            base_url=API_BASE_URL,
        )
        logger.info(
            "File store initialized mode=s3 bucket=%s endpoint=%s",
            config["s3Bucket"],
            config.get("s3Endpoint", ""),
        )
    else:
        _store = BuiltinStore(API_BASE_URL, ttl=3600)
        logger.info("File store initialized mode=builtin")


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
