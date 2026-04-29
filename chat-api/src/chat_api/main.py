"""FastAPI entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import load_config
from .routes import health as health_routes
from .routes import sessions as session_routes


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    _setup_logging(cfg.log_level)
    app.state.cfg = cfg
    logging.getLogger(__name__).info(
        "chat-api startup: foundry=%s model=%s bypass_auth=%s",
        cfg.foundry_project_endpoint, cfg.model_deployment_name, cfg.dev_bypass_auth,
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="multipersonworkflow chat-api", version="0.9.2", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # dev: tighten before production
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    app.include_router(health_routes.router)
    app.include_router(session_routes.router)
    return app


app = create_app()
