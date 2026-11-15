"""``stockpot`` command line: run the server (and, from M3, manage users, households and API keys)."""

import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point of the ``stockpot`` script."""
    parser = argparse.ArgumentParser(prog="stockpot", description="Stockpot groceries service")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host", help="bind address (default: STOCKPOT_HOST)")
    serve.add_argument("--port", type=int, help="port (default: STOCKPOT_PORT)")
    args = parser.parse_args(argv)

    if args.command == "serve":
        _serve(args.host, args.port)


def _serve(host: str | None, port: int | None) -> None:
    # Imported lazily: the core library and `stockpot --help` must not need the service extra.
    import uvicorn

    from stockpot.service.app import create_app
    from stockpot.service.logging import configure_logging
    from stockpot.service.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(create_app(settings), host=host or settings.host, port=port or settings.port, log_config=None)
