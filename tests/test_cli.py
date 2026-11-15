import pytest

from emile import cli


def test_serve_passes_settings_and_overrides_to_uvicorn(monkeypatch):
    calls = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.update(kw))
    monkeypatch.setenv("EMILE_PORT", "9123")
    from emile.service.settings import get_settings

    get_settings.cache_clear()
    try:
        cli.main(["serve", "--host", "192.0.2.10"])
    finally:
        get_settings.cache_clear()
    assert calls == {"host": "192.0.2.10", "port": 9123, "log_config": None}


def test_command_is_required():
    with pytest.raises(SystemExit):
        cli.main([])
