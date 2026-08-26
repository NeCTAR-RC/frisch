from datetime import datetime

from frisch.db import queries
from frisch.ingest import ingest
from frisch import model

T1 = datetime(2026, 1, 1)
T2 = datetime(2026, 2, 1)

ENVS = ["test", "prod"]


def obs(env, service, version, ref, when=T1, **kwargs):
    defaults = dict(
        source="argocd",
        env=env,
        source_key=f"main/{kwargs.pop('stem', service)}",
        service=service,
        version=version,
        changed_at=when,
        ref=ref,
    )
    defaults.update(kwargs)
    return model.Observation(**defaults)


def seed(session, observations_by_env):
    for env, observations in observations_by_env.items():
        ingest(session, "argocd", env, observations)
    session.commit()


def row(data, service):
    return next(r for r in data["rows"] if r["service"] == service)


def test_matrix_differs_and_dates(session):
    seed(
        session,
        {
            "test": [obs("test", "keystone", "2.2.2", "t1", T2)],
            "prod": [obs("prod", "keystone", "2.2.1", "p1", T1)],
        },
    )
    data = queries.matrix(session, ENVS)
    keystone = row(data, "keystone")
    assert keystone["differs"] is True
    assert keystone["environments"]["test"]["version"] == "2.2.2"
    assert keystone["environments"]["prod"]["version"] == "2.2.1"
    assert keystone["environments"]["test"]["changed_at"].startswith(
        "2026-02-01"
    )


def test_matrix_agreement(session):
    seed(
        session,
        {
            "test": [obs("test", "glance", "1.0", "t1")],
            "prod": [obs("prod", "glance", "1.0", "p1")],
        },
    )
    data = queries.matrix(session, ENVS)
    assert row(data, "glance")["differs"] is False


def test_matrix_env_only_service(session):
    seed(session, {"prod": [obs("prod", "mirrors", "25.0.22", "p1")]})
    data = queries.matrix(session, ENVS)
    mirrors = row(data, "mirrors")
    assert "test" not in mirrors["environments"]
    assert mirrors["differs"] is False


def test_matrix_mixed_instances(session):
    seed(
        session,
        {
            "prod": [
                obs(
                    "prod",
                    "aardvark",
                    "1.2.0",
                    "p1",
                    stem="aardvark-qld",
                    instance="qld",
                ),
                obs(
                    "prod",
                    "aardvark",
                    "1.1.0",
                    "p2",
                    stem="aardvark-monash",
                    instance="monash",
                ),
            ]
        },
    )
    data = queries.matrix(session, ENVS)
    prod = row(data, "aardvark")["environments"]["prod"]
    assert prod["mixed"] is True
    assert prod["version"] is None
    assert prod["versions"] == ["1.1.0", "1.2.0"]
    assert prod["instances"] == 2


def test_matrix_instance_filter(session):
    seed(
        session,
        {
            "prod": [
                obs("prod", "keystone", "2.2.1", "p1"),
                obs(
                    "prod",
                    "prometheus",
                    "3.1.0",
                    "p2",
                    stem="prometheus-capi",
                    instance="capi",
                ),
                obs("prod", "prometheus", "3.0.0", "p3"),
            ]
        },
    )
    data = queries.matrix(session, ENVS)
    assert data["instances"] == ["capi"]
    # Unfiltered, prometheus aggregates both instances.
    assert row(data, "prometheus")["environments"]["prod"]["instances"] == 2

    capi = queries.matrix(session, ENVS, instance="capi")
    # The instance list is unfiltered so the UI keeps its choices.
    assert capi["instances"] == ["capi"]
    assert [r["service"] for r in capi["rows"]] == ["prometheus"]
    prod = row(capi, "prometheus")["environments"]["prod"]
    assert prod["version"] == "3.1.0"
    assert prod["mixed"] is False
    assert prod["instances"] == 1

    assert queries.matrix(session, ENVS, instance="nope")["rows"] == []


def test_matrix_hides_service_removed_everywhere(session):
    seed(
        session,
        {
            "prod": [
                obs("prod", "murano", "1.0", "p1", when=T1),
                obs("prod", "murano", None, "p2", when=T2),
            ]
        },
    )
    data = queries.matrix(session, ENVS)
    # Removed in prod and never present in test: not current state, so the
    # row is dropped from the matrix (still reachable via detail/history).
    assert not [r for r in data["rows"] if r["service"] == "murano"]
    assert queries.service_detail(session, "murano") is not None


def test_matrix_shows_service_removed_in_one_env(session):
    seed(
        session,
        {
            "test": [obs("test", "trivy-operator", "1.0", "t1", when=T1)],
            "prod": [
                obs("prod", "trivy-operator", "1.0", "p1", when=T1),
                obs("prod", "trivy-operator", None, "p2", when=T2),
            ],
        },
    )
    data = queries.matrix(session, ENVS)
    envs = row(data, "trivy-operator")["environments"]
    assert envs["test"]["version"] == "1.0"
    assert envs["prod"]["removed"] is True
    assert envs["prod"]["version"] is None


def test_matrix_components_do_not_pollute(session):
    seed(
        session,
        {
            "prod": [
                obs("prod", "mariadb-operator", "26.6.0", "p1"),
                obs(
                    "prod",
                    "mariadb-operator",
                    "26.5.0",
                    "p1",
                    component="crds",
                ),
            ]
        },
    )
    data = queries.matrix(session, ENVS)
    prod = row(data, "mariadb-operator")["environments"]["prod"]
    # Only the primary component feeds the matrix headline.
    assert prod["version"] == "26.6.0"
    assert prod["mixed"] is False


def test_service_detail_and_history(session):
    seed(
        session,
        {
            "test": [
                obs("test", "keystone", "2.2.1", "t1", T1),
                obs("test", "keystone", "2.2.2", "t2", T2),
            ],
            "prod": [obs("prod", "keystone", "2.2.1", "p1", T1)],
        },
    )
    detail = queries.service_detail(session, "keystone")
    assert len(detail["instances"]) == 2

    history = queries.service_history(session, "keystone")
    assert [e["version"] for e in history] == ["2.2.2", "2.2.1", "2.2.1"]
    assert history[0]["previous_version"] == "2.2.1"

    test_only = queries.service_history(session, "keystone", env="test")
    assert {e["env"] for e in test_only} == {"test"}

    assert queries.service_detail(session, "nope") is None


def test_status(session, session_factory):
    from frisch.db.models import CollectorRun

    with session_factory() as writer:
        writer.add(
            CollectorRun(
                source="argocd",
                env="test",
                started_at=T1,
                finished_at=T1,
                status="success",
                stats={"events": 3},
            )
        )
        writer.add(
            CollectorRun(
                source="deb",
                env="prod",
                started_at=T2,
                finished_at=T2,
                status="unavailable",
                error="no package inventory",
            )
        )
        writer.commit()
    data = queries.status(session)
    by_source = {(s["source"], s["env"]): s for s in data["sources"]}
    assert by_source[("argocd", "test")]["status"] == "success"
    assert by_source[("argocd", "test")]["last_success_at"] is not None
    deb = by_source[("deb", "prod")]
    assert deb["status"] == "unavailable"
    assert deb["last_success_at"] is None
