from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import EDITION

logger = logging.getLogger("edition")


def register_ee(app: FastAPI) -> None:
    if EDITION != "ee":
        return
    from ee.backend import register_routes, register_middleware
    register_middleware(app)
    register_routes(app)
    logger.info("EE edition loaded")
