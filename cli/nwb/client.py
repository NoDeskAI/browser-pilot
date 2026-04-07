from __future__ import annotations

from typing import Any

import httpx

from nwb.config import get

_TIMEOUT = 60.0


def _url(path: str, *, api_url: str = "") -> str:
    base = api_url or get("api_url") or "http://localhost:8000"
    return f"{base.rstrip('/')}{path}"


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT)


def post(path: str, body: dict | None = None, *, api_url: str = "") -> dict:
    with _client() as c:
        r = c.post(_url(path, api_url=api_url), json=body or {})
        r.raise_for_status()
        return r.json()


def get_request(path: str, params: dict | None = None, *, api_url: str = "") -> dict:
    with _client() as c:
        r = c.get(_url(path, api_url=api_url), params=params or {})
        r.raise_for_status()
        return r.json()
