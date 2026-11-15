from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from emile import __version__
from emile.service import migrate, tables  # noqa: F401
from emile.service.db import Base, Database


def test_health_reports_ok_version_and_database(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "version": __version__, "database": "ok"}


def test_health_is_503_when_the_database_is_gone(app, client, monkeypatch):
    monkeypatch.setattr(app.state.db, "ping", lambda: False)
    res = client.get("/health")
    assert res.status_code == 503
    assert res.json()["database"] == "error"


def test_openapi_documents_the_api(client):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "Emile"
    for path in (
        "/v1/lists",
        "/v1/lists/current",
        "/v1/lists/{list_id}/items/{index}/toggle",
        "/v1/stock",
        "/v1/stock/to-check",
        "/v1/stock/{entry_id}/answer",
        "/v1/stock/expire",
        "/v1/admin/users",
    ):
        assert path in spec["paths"]  # fmt: skip
    assert "HTTPBearer" in spec["components"]["securitySchemes"]


def test_migrations_from_empty_match_the_models(database_url):
    db = Database(database_url)
    migrate.upgrade(db)
    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []
    assert set(inspect(db.engine).get_table_names()) >= {"users", "api_keys", "households", "memberships",
                                                        "shopping_lists", "list_items", "stock_entries"}  # fmt: skip
    migrate.upgrade(db)  # idempotent
    migrate.downgrade(db, "base")
    assert set(inspect(db.engine).get_table_names()) <= {"alembic_version"}
    db.dispose()
