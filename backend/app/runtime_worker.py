from __future__ import annotations

import secrets

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.config import BROWSER_RUNTIME_CONTROL_TOKEN
from app.runtime_control import (
    RuntimeCommandRejected,
    run_local_command,
    validate_runtime_command,
)

app = FastAPI(title="Browser Pilot Runtime Worker")


class RuntimeCommandBody(BaseModel):
    cmd: str
    timeout: float = 30


def _authorize(authorization: str | None) -> None:
    if not BROWSER_RUNTIME_CONTROL_TOKEN:
        raise HTTPException(status_code=503, detail="Runtime control token is not configured")
    expected = f"Bearer {BROWSER_RUNTIME_CONTROL_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid runtime control token")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/internal/runtime/command")
async def run_command(body: RuntimeCommandBody, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        validate_runtime_command(body.cmd)
    except RuntimeCommandRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    stdout, stderr, returncode = await run_local_command(body.cmd, timeout=body.timeout)
    return {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
    }
