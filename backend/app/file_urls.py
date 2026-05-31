from __future__ import annotations

import hashlib
import hmac
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from app.config import API_BASE_URL, FILE_DOWNLOAD_URL_TTL_SECONDS, JWT_SECRET


def file_url_ext(filename: str) -> str:
    suffix = Path(str(filename or "")).suffix or ".bin"
    return suffix.lstrip(".") or "bin"


def attachment_content_disposition(filename: str) -> str:
    raw = Path(str(filename or "file")).name.strip() or "file"
    fallback = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw).strip(" .") or "file"
    fallback = fallback.replace("\\", "_").replace('"', "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(raw)}"


def _signature_message(file_id: str, ext: str, expires: int) -> bytes:
    return f"{file_id}.{ext}:{expires}".encode()


def sign_file_download(file_id: str, ext: str, expires: int) -> str:
    return hmac.new(
        JWT_SECRET.encode(),
        _signature_message(file_id, ext, expires),
        hashlib.sha256,
    ).hexdigest()


def verify_file_download_signature(
    file_id: str,
    ext: str,
    expires: int,
    signature: str,
    *,
    now: int | None = None,
) -> bool:
    if expires < (now if now is not None else int(time.time())):
        return False
    expected = sign_file_download(file_id, ext, expires)
    return hmac.compare_digest(expected, signature or "")


def backend_download_url(
    file_id: str,
    filename: str,
    *,
    expires_in: int = FILE_DOWNLOAD_URL_TTL_SECONDS,
) -> str:
    ext = file_url_ext(filename)
    expires = int(time.time()) + expires_in
    query = urlencode(
        {
            "expires": str(expires),
            "signature": sign_file_download(file_id, ext, expires),
        }
    )
    return f"{API_BASE_URL.rstrip('/')}/api/files/{file_id}.{ext}?{query}"
