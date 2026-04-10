from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import APP_TITLE
from app.logging_config import setup_logging
from app.routes.chat import router as chat_router
from app.routes.docker import router as docker_router
from app.routes.models import router as models_router
from app.routes.browser import router as browser_router
from app.routes.sessions import router as sessions_router
from app.routes.cli import router as cli_router
from app.routes.settings import router as settings_router
from app.routes.files import router as files_router

setup_logging()
logger = logging.getLogger("access")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_task = asyncio.create_task(db.init_db())
    yield
    init_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await init_task
    await db.close_db()


app = FastAPI(title=f"{APP_TITLE} API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SKIP_LOG_PATHS = {"/api/docker/status", "/healthz", "/readyz"}


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


@app.get("/healthz")
async def _liveness():
    return JSONResponse({"status": "ok"})


@app.get("/readyz")
async def _readiness():
    if db.is_ready():
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "waiting for database"}, status_code=503)


app.include_router(browser_router)
app.include_router(chat_router)
app.include_router(docker_router)
app.include_router(models_router)
app.include_router(sessions_router)
app.include_router(cli_router)
app.include_router(settings_router)
app.include_router(files_router)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

if _STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str):
        file = _STATIC_DIR / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_STATIC_DIR / "index.html")
