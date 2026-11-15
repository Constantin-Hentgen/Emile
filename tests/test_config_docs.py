"""Configuration must stay documented: every setting and every compose variable appears in .env.example."""

import re
from pathlib import Path

from emile.service.settings import Settings

ROOT = Path(__file__).resolve().parent.parent


def documented() -> set[str]:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, flags=re.MULTILINE))


def test_every_setting_is_in_env_example():
    expected = {f"EMILE_{name.upper()}" for name in Settings.model_fields}
    assert expected <= documented()


def test_every_compose_variable_is_in_env_example():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{([A-Z0-9_]+)", compose))
    assert used <= documented()


def test_settings_read_prefixed_environment(monkeypatch):
    monkeypatch.setenv("EMILE_DATABASE_URL", "postgresql+psycopg://u:p@db/emile")
    monkeypatch.setenv("EMILE_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///ignored.db")  # unprefixed names belong to other services
    s = Settings(_env_file=None)
    assert s.database_url == "postgresql+psycopg://u:p@db/emile"
    assert s.log_level == "DEBUG"
