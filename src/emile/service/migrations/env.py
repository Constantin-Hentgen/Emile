"""Alembic environment. The connection (or URL) is passed in by :mod:`emile.service.migrate`, never read from a
config file, so the same code runs from the app, the CLI and the tests."""

from alembic import context
from sqlalchemy.engine import Connection

from emile.service import tables  # noqa: F401  (registers the tables on the metadata)
from emile.service.db import Base, make_engine

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite can only ALTER via table copies
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    engine = make_engine(config.attributes["url"])
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()
