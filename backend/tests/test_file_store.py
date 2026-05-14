import asyncio
import base64

import boto3

from app.file_store import BuiltinStore, S3Store


class FakeBody:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.presign_calls = []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = (Body, ContentType)

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        content_type = (ExtraArgs or {}).get("ContentType", "application/octet-stream")
        with open(Filename, "rb") as fh:
            self.objects[(Bucket, Key)] = (fh.read(), content_type)

    def get_object(self, Bucket, Key):
        body, content_type = self.objects[(Bucket, Key)]
        return {"Body": FakeBody(body), "ContentType": content_type}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        self.presign_calls.append(
            {
                "ClientMethod": ClientMethod,
                "Params": Params,
                "ExpiresIn": ExpiresIn,
            }
        )
        return f"http://public-storage:9000/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


def test_s3_store_returns_presigned_public_url_and_can_read(monkeypatch):
    client = FakeS3Client()

    class FakeSession:
        def __init__(self, **_kwargs):
            pass

        def client(self, *_args, **_kwargs):
            return client

    monkeypatch.setattr(boto3, "Session", FakeSession)
    store = S3Store(
        bucket="browser-pilot",
        region="us-east-1",
        access_key="browserpilot",
        secret_key="secret",
        endpoint="http://object-storage:9000",
        public_endpoint="http://public-storage:9000",
        presign=True,
        presign_expires=3600,
        base_url="http://localhost:8000",
    )

    b64 = base64.b64encode(b"png-bytes").decode()
    saved = asyncio.run(store.save(b64, "session-1"))
    file_id = saved["url"].removeprefix("http://localhost:8000/api/files/").removesuffix(".png")
    loaded = asyncio.run(store.get(file_id))
    key = next(iter(client.objects))[1]
    url = asyncio.run(
        store.download_url(
            key=key,
            file_id="file-1",
            filename="screenshot.png",
            expires_in=900,
        )
    )
    asyncio.run(store.delete_by_key(key))

    assert saved["url"].startswith("http://localhost:8000/api/files/")
    assert url.startswith("http://public-storage:9000/")
    assert "object-storage:9000" not in url
    assert client.presign_calls[0]["ClientMethod"] == "get_object"
    assert client.presign_calls[0]["ExpiresIn"] == 900
    assert client.presign_calls[0]["Params"]["ResponseContentDisposition"].startswith("attachment;")
    assert loaded == (b"png-bytes", "image/png")
    assert client.objects == {}


def test_builtin_store_serves_cached_file():
    store = BuiltinStore("http://localhost:8000")
    b64 = base64.b64encode(b"png-bytes").decode()

    saved = asyncio.run(store.save(b64, "session-1"))
    file_id = saved["url"].removeprefix("http://localhost:8000/api/files/").removesuffix(".png")
    loaded = asyncio.run(store.get(file_id))
    key = next(iter(store._cache))
    asyncio.run(store.delete_by_key(key))

    assert loaded == (b"png-bytes", "image/png")
    assert store._cache == {}
