"""Logging setup shared by the CLI and the app, so uvicorn and Stockpot messages use one format and one level."""

import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    """Configure the root logger; uvicorn's loggers propagate to it because the CLI passes ``log_config=None``."""
    logging.basicConfig(level=level, format=FORMAT, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
