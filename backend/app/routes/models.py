from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("agent.routes.models")
router = APIRouter()

ANTHROPIC_PRESET_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-3-5-haiku-20241022",
]


class ModelsRequest(BaseModel):
    baseUrl: str | None = None
    apiKey: str | None = None
    apiType: str | None = None


@router.post("/api/ai/models")
async def list_models(body: ModelsRequest):
    if not body.apiKey or not body.baseUrl:
        return {"models": []}

    if body.apiType == "anthropic":
        return {"models": ANTHROPIC_PRESET_MODELS}

    base = body.baseUrl.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {body.apiKey}"})
            if resp.status_code != 200:
                return {"models": [], "error": f"upstream {resp.status_code}"}
            data = resp.json()
            ids = sorted(m["id"] for m in (data.get("data") or []))
            return {"models": ids}
    except Exception as exc:
        logger.warning("/api/ai/models error: %s", exc)
        return {"models": [], "error": str(exc)}
