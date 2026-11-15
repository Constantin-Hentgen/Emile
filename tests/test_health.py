from fastapi.testclient import TestClient

from stockpot import __version__
from stockpot.service.app import create_app
from stockpot.service.settings import Settings


def test_health_reports_ok_and_version(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/test.db"))
    with TestClient(app) as client:
        res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "version": __version__}


def test_openapi_is_served():
    with TestClient(create_app(Settings())) as client:
        assert client.get("/openapi.json").json()["info"]["title"] == "Stockpot"
