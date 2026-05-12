import asyncio
import json

from app import file_store
from app.routes import settings


class FakePool:
    def __init__(self, value=None):
        self.value = value
        self.saved = None

    async def fetchrow(self, _query, _key):
        if self.value is None:
            return None
        return {"value": json.dumps(self.value)}

    async def execute(self, _query, _key, value):
        self.saved = json.loads(value)


def test_get_storage_settings_masks_secret(monkeypatch):
    pool = FakePool(
        {
            "storage": "s3",
            "s3Bucket": "browser-pilot",
            "s3Region": "us-east-1",
            "s3AccessKey": "browserpilot",
            "s3SecretKey": "secret",
            "s3Endpoint": "http://object-storage:9000",
            "s3Presign": True,
            "s3PresignExpires": 3600,
        }
    )
    monkeypatch.setattr(settings, "get_pool", lambda: pool)

    result = asyncio.run(settings.get_storage_settings())

    assert result["s3SecretKey"] == ""
    assert result["s3SecretConfigured"] is True


def test_save_storage_settings_reuses_existing_secret(monkeypatch):
    pool = FakePool(
        {
            "storage": "s3",
            "s3Bucket": "old-bucket",
            "s3Region": "us-east-1",
            "s3AccessKey": "old-access",
            "s3SecretKey": "old-secret",
            "s3Endpoint": "http://object-storage:9000",
            "s3Presign": True,
            "s3PresignExpires": 3600,
        }
    )
    verified = {}

    async def fake_verify(cfg):
        verified["secret"] = cfg.s3SecretKey

    async def fake_invalidate():
        return None

    monkeypatch.setattr(settings, "get_pool", lambda: pool)
    monkeypatch.setattr(settings, "_verify_s3_connection", fake_verify)
    monkeypatch.setattr(file_store, "invalidate_store", fake_invalidate)

    body = settings.StorageConfig(
        storage="s3",
        s3Bucket="new-bucket",
        s3Region="us-east-1",
        s3AccessKey="new-access",
        s3SecretKey="",
        s3Endpoint="https://s3.example.com",
    )

    result = asyncio.run(settings.save_storage_settings(body))

    assert result == {"ok": True}
    assert verified["secret"] == "old-secret"
    assert pool.saved["s3SecretKey"] == "old-secret"
    assert "s3SecretConfigured" not in pool.saved
