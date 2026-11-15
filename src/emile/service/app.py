"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from emile import __version__
from emile.service.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class Health(BaseModel):
    """Liveness payload: enough for Docker's healthcheck and to see which version is deployed."""

    status: Literal["ok"] = "ok"
    version: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Settings are injectable so tests can point at a temporary database without env juggling."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("Emile %s starting", __version__)
        yield
        logger.info("Emile stopping")

    app = FastAPI(
        title="Emile",
        version=__version__,
        summary="Shopping lists, kitchen stock and batch-cooked boxes. Deterministic: no LLM inside.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.get("/health", tags=["operations"])
    def health() -> Health:
        return Health(version=__version__)

    return app
