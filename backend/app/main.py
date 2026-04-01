from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.logging_config import setup_logging
from app.routes.chat import router as chat_router
from app.routes.docker import router as docker_router
from app.routes.models import router as models_router

setup_logging()
logger = logging.getLogger("access")

app = FastAPI(title="NoDeskPane Agent Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SKIP_LOG_PATHS = {"/api/docker/status"}


@app.middleware("http")
async def request_logging(request: Request, call_next):
    if request.url.path in SKIP_LOG_PATHS:
        return await call_next(request)

    t0 = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - t0

    logger.info(
        "%s %s -> %d (%.1fs)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


app.include_router(chat_router)
app.include_router(docker_router)
app.include_router(models_router)
