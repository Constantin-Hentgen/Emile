"""The core library must stay usable without the service stack (see the package docstring)."""

import subprocess
import sys

FORBIDDEN = {"fastapi", "starlette", "uvicorn", "sqlalchemy", "alembic", "caldav", "httpx", "requests"}


def test_core_imports_no_web_or_db_module():
    code = (
        "import pkgutil, importlib, sys, emile.core as c\n"
        "for m in pkgutil.walk_packages(c.__path__, 'emile.core.'): importlib.import_module(m.name)\n"
        "print(' '.join(sorted({m.split('.')[0] for m in sys.modules})))"
    )
    loaded = set(
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True).stdout.split()
    )
    assert not loaded & FORBIDDEN


def test_cli_help_needs_no_service_import():
    code = "import sys, emile.cli\nprint(' '.join(sorted({m.split('.')[0] for m in sys.modules})))"
    loaded = set(
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True).stdout.split()
    )
    assert not loaded & FORBIDDEN
