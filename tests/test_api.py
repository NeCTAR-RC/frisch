from datetime import datetime

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine

from frisch.config import Config
from frisch.db.models import Base
from frisch.db.session import make_session_factory
from frisch.ingest import ingest
from frisch import model
from frisch.web.app import create_app


@pytest.fixture
def client(tmp_path):
    url = f"sqlite:///{tmp_path}/frisch.db"
    Base.metadata.create_all(create_engine(url))
    config = Config()
    config.database_url = url

    with make_session_factory(url)() as session:
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
                    version="2.2.2",
                    changed_at=datetime(2026, 2, 1),
                    ref="t1",
                )
            ],
        )
        ingest(
            session,
            "argocd",
            "prod",
            [
                model.Observation(
                    source="argocd",
                    env="prod",
                    source_key="main/keystone",
                    service="keystone",
                    version="2.2.1",
                    changed_at=datetime(2026, 1, 1),
                    ref="p1",
                )
            ],
        )
        session.commit()
    return TestClient(create_app(config))


def test_matrix_endpoint(client):
    data = client.get("/api/v1/matrix").json()
    assert data["environments"] == ["test", "prod"]
    (keystone,) = data["rows"]
    assert keystone["service"] == "keystone"
    assert keystone["differs"] is True


def test_services_endpoints(client):
    assert client.get("/api/v1/services").json() == {"services": ["keystone"]}
    detail = client.get("/api/v1/services/keystone").json()
    assert len(detail["instances"]) == 2
    assert client.get("/api/v1/services/nope").status_code == 404


def test_history_endpoint(client):
    data = client.get("/api/v1/services/keystone/history?env=test").json()
    assert [e["version"] for e in data["events"]] == ["2.2.2"]
    assert client.get("/api/v1/services/nope/history").status_code == 404


def test_status_and_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    status = client.get("/api/v1/status").json()
    assert "sources" in status
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert b"frisch_version_events_total" in metrics.content
