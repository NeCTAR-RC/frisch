"""App factory concerns: health, metrics, the SPA mount and ``frisch-web``."""

from datetime import datetime
import os

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine

from frisch.config import Config, ENV_PREFIX
from frisch.db.models import Base, CollectorRun
from frisch.db.session import make_session_factory
from frisch.ingest import ingest
from frisch import model
from frisch.web import app as app_mod


@pytest.fixture
def config(tmp_path):
    url = f"sqlite:///{tmp_path}/frisch.db"
    Base.metadata.create_all(create_engine(url))
    cfg = Config()
    cfg.database_url = url
    return cfg


def test_healthz(config):
    client = TestClient(app_mod.create_app(config))
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_app_falls_back_to_load_config(config, monkeypatch):
    monkeypatch.setattr(app_mod, "load_config", lambda: config)
    client = TestClient(app_mod.create_app())
    assert client.get("/healthz").status_code == 200


def test_metrics_report_freshness_and_event_count(config):
    with make_session_factory(config.database_url)() as session:
        ingest(
            session,
            "argocd",
            "test",
            [
                model.Observation(
                    source="argocd",
                    env="test",
                    source_key="main/keystone",
                    service="keystone",
                    version="1.0.0",
                    changed_at=datetime(2026, 2, 1),
                    ref="t1",
                )
            ],
        )
        session.add_all(
            [
                CollectorRun(
                    source="argocd",
                    env="test",
                    started_at=datetime(2026, 2, 1),
                    finished_at=datetime(2026, 2, 1, 1),
                    status="success",
                ),
                # Unfinished and failed runs must not count as a success.
                CollectorRun(
                    source="argocd",
                    env="prod",
                    started_at=datetime(2026, 2, 1),
                    status="running",
                ),
                CollectorRun(
                    source="deb",
                    env="test",
                    started_at=datetime(2026, 2, 1),
                    finished_at=datetime(2026, 2, 1, 1),
                    status="error",
                    error="boom",
                ),
            ]
        )
        session.commit()

    client = TestClient(app_mod.create_app(config))
    body = client.get("/metrics").text

    assert (
        'frisch_collector_last_success_timestamp{env="test",source="argocd"}'
        in body
    )
    assert 'env="prod"' not in body
    assert 'source="deb"' not in body
    assert "frisch_version_events_total 1.0" in body


@pytest.fixture
def spa_client(config, tmp_path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html>shell")
    (static / "assets" / "app.js").write_text("console.log(1)")
    config.web_static = static
    return TestClient(app_mod.create_app(config))


def test_spa_serves_real_files(spa_client):
    assert spa_client.get("/assets/app.js").text == "console.log(1)"


def test_spa_falls_back_to_the_shell_for_client_routes(spa_client):
    response = spa_client.get("/services/keystone")
    assert response.status_code == 200
    assert "shell" in response.text


def test_spa_does_not_shadow_the_api(spa_client):
    assert spa_client.get("/api/v1/nope").status_code == 404


def test_spa_does_not_escape_the_static_dir(spa_client, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    response = spa_client.get("/../secret.txt")
    assert "private" not in response.text


def test_spa_404s_without_an_index(config, tmp_path):
    static = tmp_path / "empty"
    static.mkdir()
    config.web_static = static
    client = TestClient(app_mod.create_app(config))
    assert client.get("/anything").status_code == 404


def test_main_starts_uvicorn_with_the_factory(tmp_path, monkeypatch):
    import uvicorn

    called = {}
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda target, **kwargs: called.update(target=target, **kwargs),
    )
    # setenv (not delenv) so monkeypatch restores the developer's own
    # environment after main() writes to it.
    monkeypatch.setenv(ENV_PREFIX + "CONFIG", "")
    monkeypatch.setenv(ENV_PREFIX + "WEB_STATIC", "")

    rc = app_mod.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
            "--config",
            str(tmp_path / "frisch.yaml"),
            "--static-dir",
            str(tmp_path / "static"),
            "--reload",
        ]
    )

    assert rc == 0
    assert called["target"] == "frisch.web.app:create_app"
    assert called["factory"] is True
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 9000
    assert called["reload"] is True
    # The reloader subprocess re-reads these from the environment.
    assert os.environ[ENV_PREFIX + "CONFIG"].endswith("frisch.yaml")
    assert os.environ[ENV_PREFIX + "WEB_STATIC"].endswith("static")
