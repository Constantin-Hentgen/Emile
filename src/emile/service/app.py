"""FastAPI application factory."""

import datetime
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from emile import __version__
from emile.schemas import ErrorBody, Health
from emile.service import errors, migrate
from emile.service.db import Database
from emile.service.deps import Clock
from emile.service.routes import account, admin, lists, stock
from emile.service.settings import Settings, get_settings

logger = logging.getLogger(__name__)

DESCRIPTION = """
Shopping lists, kitchen stock and batch-cooked boxes, shared by a household.

**Deterministic: no LLM inside.** Clients (a person, a script, a chat assistant's language model) decide *what* to
buy or cook; Emile stores, edits, checks, expires and syncs, with explicit rules.

Authenticate with `Authorization: Bearer <API key>`. Users in several households choose one with the
`Emile-Household` header. Errors are `{"error": <code>, "detail": <text>}`.
"""


def system_clock() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def create_app(settings: Settings | None = None, *, clock: Clock = system_clock) -> FastAPI:
    """Build the app. Settings and clock are injectable so tests use a temporary database and a frozen time."""
    settings = settings or get_settings()
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("Emile %s starting", __version__)
        if settings.auto_migrate:
            migrate.upgrade(db)
        if settings.admin_key is None:
            logger.info("admin API disabled (EMILE_ADMIN_KEY unset)")
        yield
        db.dispose()
        logger.info("Emile stopping")

    app = FastAPI(
        title="Emile",
        version=__version__,
        summary="Shopping lists, kitchen stock and batch-cooked boxes. Deterministic: no LLM inside.",
        description=DESCRIPTION,
        lifespan=lifespan,
        responses={
            400: {"model": ErrorBody},
            401: {"model": ErrorBody},
            404: {"model": ErrorBody},
            409: {"model": ErrorBody},
            422: {"model": ErrorBody},
        },
    )
    app.state.settings = settings
    app.state.db = db
    app.state.clock = clock
    errors.install(app)

    @app.get("/health", tags=["operations"], responses={503: {"model": Health}})
    def health(response: Response) -> Health:
        """Liveness and readiness: 200 when the database answers, 503 otherwise."""
        if db.ping():
            return Health(status="ok", version=__version__, database="ok")
        response.status_code = 503
        return Health(status="error", version=__version__, database="error")

    for router in (account.router, lists.router, stock.router, admin.router):
        app.include_router(router)
    return app
