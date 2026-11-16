"""FastAPI application factory."""

import asyncio
import datetime
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from emile import __version__
from emile.recipes import build_sources
from emile.schemas import ErrorBody, Health
from emile.service import errors, migrate, sync
from emile.service.db import Database
from emile.service.deps import Clock
from emile.service.remote import RemoteFactory, caldav_factory
from emile.service.routes import account, admin, calendar, imports, lists, recipes, stock
from emile.service.settings import Settings, get_settings
from emile.service.vault import Vault

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


async def sync_loop(app: FastAPI, interval: int) -> None:
    """Every ``interval`` seconds, bring every household's calendar in line with its list."""
    while True:
        await asyncio.sleep(interval)
        try:
            results = await asyncio.to_thread(
                sync.sync_all, app.state.db, app.state.vault, app.state.remote_factory, app.state.clock()
            )
            for household_id, result in results.items():
                if isinstance(result, str):
                    logger.warning("household %s: calendar not synced: %s", household_id, result)
        except Exception:
            logger.exception("calendar sync pass failed")


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock = system_clock,
    remote_factory: RemoteFactory = caldav_factory,
) -> FastAPI:
    """Build the app. Settings, clock and calendar connection are injectable so tests use a temporary database,
    a frozen time and an in-memory calendar."""
    settings = settings or get_settings()
    db = Database(settings.database_url)
    vault = Vault(settings.secret_key.get_secret_value()) if settings.secret_key else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("Emile %s starting", __version__)
        if settings.auto_migrate:
            migrate.upgrade(db)
        if settings.admin_key is None:
            logger.info("admin API disabled (EMILE_ADMIN_KEY unset)")
        task = None
        if vault is None:
            logger.info("calendar sync disabled (EMILE_SECRET_KEY unset)")
        elif settings.sync_interval > 0:
            task = asyncio.create_task(sync_loop(app, settings.sync_interval))
        yield
        if task is not None:
            task.cancel()
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
    app.state.vault = vault
    app.state.remote_factory = remote_factory
    app.state.recipe_sources = build_sources(settings.recipe_source_names, timeout=settings.http_timeout)
    errors.install(app)

    @app.get("/health", tags=["operations"], responses={503: {"model": Health}})
    def health(response: Response) -> Health:
        """Liveness and readiness: 200 when the database answers, 503 otherwise."""
        if db.ping():
            return Health(status="ok", version=__version__, database="ok")
        response.status_code = 503
        return Health(status="error", version=__version__, database="error")

    for router in (
        account.router,
        lists.router,
        stock.router,
        recipes.router,
        imports.router,
        calendar.router,
        admin.router,
    ):
        app.include_router(router)
    return app
