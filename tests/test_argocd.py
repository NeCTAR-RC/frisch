from datetime import datetime
from pathlib import Path

from frisch.collectors.argocd import ArgocdCollector
from frisch.config import ArgocdConfig, Config, GitRepoConfig
from frisch import model
from frisch.model import ServiceIdentity

FIXTURES = Path(__file__).parent / "fixtures" / "argocd"


def app_yaml(name: str, chart: str, version: str) -> str:
    return f"""apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {name}
spec:
  source:
    repoURL: registry.rc.nectar.org.au/nectar-helm
    chart: {chart}
    targetRevision: {version}
"""


def collector(tmp_path, repo, **argocd_kwargs) -> ArgocdCollector:
    config = Config()
    config.git_cache_dir = tmp_path / "cache"
    config.argocd = ArgocdConfig(
        repos={"test": GitRepoConfig(url=str(repo.path))}, **argocd_kwargs
    )
    return ArgocdCollector(config, ServiceIdentity(config.services))


def by_key(observations, source_key, component=model.PRIMARY):
    return [
        o
        for o in observations
        if o.source_key == source_key and o.component == component
    ]


def test_backfill_versions_and_dates(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {"apps-main/keystone.yaml": app_yaml("keystone", "keystone", "2.2.0")},
        "add keystone",
        date="2026-01-01T00:00:00 +0000",
    )
    repo.commit(
        {"apps-main/keystone.yaml": app_yaml("keystone", "keystone", "2.2.1")},
        "bump keystone",
        date="2026-02-01T00:00:00 +0000",
    )
    result = collector(tmp_path, repo).collect("test", cursors)

    keystone = by_key(result.observations, "main/keystone")
    assert [o.version for o in keystone] == ["2.2.0", "2.2.1"]
    assert keystone[0].changed_at == datetime(2026, 1, 1)
    assert keystone[1].changed_at == datetime(2026, 2, 1)
    assert keystone[1].precision == model.EXACT
    assert keystone[1].meta["subject"] == "bump keystone"
    assert cursors.get("argocd:test") is not None


def test_incremental_collect(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {"apps-main/keystone.yaml": app_yaml("keystone", "keystone", "2.2.0")},
        "add",
    )
    coll = collector(tmp_path, repo)
    first = coll.collect("test", cursors)
    assert len(first.observations) == 1

    repo.commit(
        {"apps-main/keystone.yaml": app_yaml("keystone", "keystone", "2.2.1")},
        "bump",
    )
    second = coll.collect("test", cursors)
    assert [o.version for o in second.observations] == ["2.2.1"]

    third = coll.collect("test", cursors)
    assert third.observations == []


def test_multi_source_components(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {
            "apps-main/mariadb-operator.yaml": (
                FIXTURES / "mariadb-operator.yaml"
            ).read_text()
        },
        "add",
    )
    result = collector(tmp_path, repo).collect("test", cursors)
    primary = by_key(result.observations, "main/mariadb-operator")
    crds = by_key(
        result.observations, "main/mariadb-operator", "mariadb-operator-crds"
    )
    assert primary[0].version == "26.6.0"
    assert primary[0].meta["chart"] == "mariadb-operator"
    assert crds[0].version == "26.6.0"


def test_head_app_is_floating(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {
            "apps-main/k8s-infra.yaml": (
                FIXTURES / "k8s-infra.yaml"
            ).read_text()
        },
        "add",
    )
    result = collector(tmp_path, repo).collect("test", cursors)
    (obs,) = by_key(result.observations, "main/k8s-infra")
    assert obs.version == "HEAD"
    assert obs.version_kind == model.FLOATING
    assert obs.meta["repo_url"].endswith("cluster-infra.git")


def test_values_version_paths(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {
            "apps-main/event-exporter.yaml": (
                FIXTURES / "event-exporter.yaml"
            ).read_text()
        },
        "add",
    )
    coll = collector(
        tmp_path,
        repo,
        app_overrides={
            "event-exporter": {"values_version_paths": ["image.tag"]}
        },
    )
    result = coll.collect("test", cursors)
    (primary,) = by_key(result.observations, "main/event-exporter")
    (image,) = by_key(result.observations, "main/event-exporter", "image")
    assert primary.version == "3.6.3"
    assert image.version == "v1.7"


def test_delete_emits_tombstone(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {"apps-main/trove.yaml": app_yaml("trove", "trove", "1.0.0")}, "add"
    )
    repo.commit({"apps-main/trove.yaml": None}, "remove trove")
    result = collector(tmp_path, repo).collect("test", cursors)
    trove = by_key(result.observations, "main/trove")
    assert [o.version for o in trove] == ["1.0.0", None]


def test_rename_keeps_source_key(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {"apps/keystone.yaml": app_yaml("keystone", "keystone", "2.0.0")},
        "add",
    )
    repo.move("apps/keystone.yaml", "apps-main/keystone.yaml", "restructure")
    result = collector(tmp_path, repo).collect("test", cursors)
    keystone = by_key(result.observations, "main/keystone")
    # Both the original add and the rename resolve to the same source_key;
    # no tombstone is emitted.
    assert [o.version for o in keystone] == ["2.0.0", "2.0.0"]
    assert not [o for o in result.observations if o.version is None]


def test_fanout_and_capi_identity(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {
            "apps-main/aardvark-qld.yaml": app_yaml(
                "aardvark-qld", "aardvark", "1.2.0"
            ),
            "apps-capi/prometheus.yaml": app_yaml(
                "prometheus", "kube-prometheus-stack", "88.5.4"
            ),
        },
        "add",
    )
    coll = collector(tmp_path, repo, fanout_services=["aardvark"])
    result = coll.collect("test", cursors)
    (aardvark,) = by_key(result.observations, "main/aardvark-qld")
    assert aardvark.service == "aardvark"
    assert aardvark.instance == "qld"
    (prometheus,) = by_key(result.observations, "capi/prometheus")
    assert prometheus.service == "prometheus"
    assert prometheus.instance == "capi"


def test_incremental_component_removal(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {
            "apps-main/mariadb-operator.yaml": (
                FIXTURES / "mariadb-operator.yaml"
            ).read_text()
        },
        "add",
    )
    coll = collector(tmp_path, repo)
    coll.collect("test", cursors)
    # Replace the multi-source app with a single-source one: the crds
    # component must be tombstoned in the incremental walk.
    repo.commit(
        {
            "apps-main/mariadb-operator.yaml": app_yaml(
                "mariadb-operator", "mariadb-operator", "27.0.0"
            )
        },
        "simplify",
    )
    result = coll.collect("test", cursors)
    gone = by_key(
        result.observations, "main/mariadb-operator", "mariadb-operator-crds"
    )
    assert [o.version for o in gone] == [None]
