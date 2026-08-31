"""Integration tests over run_collection: cursor persistence across runs,
run bookkeeping, and the non-zero-exit contract."""

import httpx
import pytest
import respx
from sqlalchemy import create_engine, select

from frisch.config import (
    ArgocdConfig,
    Config,
    GitRepoConfig,
    PuppetDBConfig,
    PuppetDBEnvConfig,
)
from frisch import collect as collect_mod
from frisch.collect import main, run_collection
from frisch.db.models import Base, CollectorRun, VersionEvent
from frisch.db.session import make_session_factory

APP = """apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: keystone
spec:
  source:
    repoURL: registry.rc.nectar.org.au/nectar-helm
    chart: keystone
    targetRevision: {version}
"""


def make_config(tmp_path, repo) -> Config:
    url = f"sqlite:///{tmp_path}/frisch.db"
    Base.metadata.create_all(create_engine(url))
    config = Config()
    config.database_url = url
    config.git_cache_dir = tmp_path / "cache"
    config.environments = ["test"]
    config.argocd = ArgocdConfig(
        repos={"test": GitRepoConfig(url=str(repo.path))}
    )
    # Only the argocd source is configured; puppet and deb have no
    # environments and are skipped.
    return config


def last_run_stats(config):
    with make_session_factory(config.database_url)() as session:
        runs = session.scalars(
            select(CollectorRun).order_by(CollectorRun.id)
        ).all()
        assert all(r.status == "success" for r in runs)
        return runs[-1].stats


def event_count(config):
    with make_session_factory(config.database_url)() as session:
        return len(session.scalars(select(VersionEvent)).all())


def test_cursor_survives_backfill_then_collect(make_repo, tmp_path):
    repo = make_repo()
    repo.commit({"apps-main/keystone.yaml": APP.format(version="1.0.0")})
    config = make_config(tmp_path, repo)

    assert run_collection(config)
    assert last_run_stats(config)["commits"] == 1

    # A backfill deletes and re-sets the cursor in one transaction; the
    # following incremental run must walk nothing.
    assert run_collection(config, backfill=True)
    assert run_collection(config)
    assert last_run_stats(config)["commits"] == 0
    assert event_count(config) == 1

    repo.commit({"apps-main/keystone.yaml": APP.format(version="1.1.0")})
    assert run_collection(config)
    assert last_run_stats(config)["commits"] == 1
    assert event_count(config) == 2


def test_failed_source_returns_false_and_is_recorded(make_repo, tmp_path):
    repo = make_repo()
    repo.commit({"apps-main/keystone.yaml": APP.format(version="1.0.0")})
    config = make_config(tmp_path, repo)
    # Point the repo somewhere nonexistent: the run fails, is recorded as
    # an error, and run_collection reports failure.
    config.argocd.repos["test"] = GitRepoConfig(url=str(tmp_path / "gone"))
    assert run_collection(config) is False
    with make_session_factory(config.database_url)() as session:
        (run,) = session.scalars(select(CollectorRun)).all()
        assert run.status == "error"
        assert run.finished_at is not None
        assert "gone" in run.error


def test_unavailable_source_is_recorded_and_fails_the_run(tmp_path):
    config = Config()
    config.database_url = f"sqlite:///{tmp_path}/frisch.db"
    Base.metadata.create_all(create_engine(config.database_url))
    config.environments = ["test"]
    config.puppetdb = PuppetDBConfig(
        environments={"test": PuppetDBEnvConfig(base_url="https://pdb")},
        package_names=["nectar-thing"],
    )

    with respx.mock:
        respx.post(url__startswith="https://pdb").mock(
            side_effect=httpx.ConnectError("no route to host")
        )
        assert run_collection(config, sources=["deb"]) is False

    with make_session_factory(config.database_url)() as session:
        (run,) = session.scalars(select(CollectorRun)).all()
        assert run.status == "unavailable"
        assert run.finished_at is not None


def config_file(tmp_path, repo) -> str:
    path = tmp_path / "frisch.yaml"
    path.write_text(
        f"database:\n  url: sqlite:///{tmp_path}/frisch.db\n"
        f"git_cache_dir: {tmp_path}/cache\n"
        "environments: [test]\n"
        "collectors:\n"
        "  argocd:\n"
        "    repos:\n"
        f"      test:\n        url: {repo.path}\n"
    )
    return str(path)


def test_main_returns_zero_on_success(make_repo, tmp_path):
    repo = make_repo()
    repo.commit({"apps-main/keystone.yaml": APP.format(version="1.0.0")})
    make_config(tmp_path, repo)  # creates the schema

    assert (
        main(["--config", config_file(tmp_path, repo), "--env", "test"]) == 0
    )
    assert event_count(Config(database_url=f"sqlite:///{tmp_path}/frisch.db"))


def test_main_returns_one_when_a_source_fails(tmp_path, monkeypatch):
    path = tmp_path / "frisch.yaml"
    path.write_text(f"database:\n  url: sqlite:///{tmp_path}/frisch.db\n")
    monkeypatch.setattr(collect_mod, "run_collection", lambda *a, **k: False)
    assert main(["--config", str(path)]) == 1


def test_main_loops_until_interrupted(tmp_path, monkeypatch):
    """--interval keeps running, and only the first pass backfills."""
    path = tmp_path / "frisch.yaml"
    path.write_text(f"database:\n  url: sqlite:///{tmp_path}/frisch.db\n")

    backfills = []

    def fake_run(config, sources=None, envs=None, backfill=False):
        backfills.append(backfill)
        return True

    def fake_sleep(seconds):
        assert seconds == 30
        if len(backfills) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(collect_mod, "run_collection", fake_run)
    monkeypatch.setattr(collect_mod.time, "sleep", fake_sleep)

    with pytest.raises(KeyboardInterrupt):
        main(["--config", str(path), "--backfill", "--interval", "30"])

    assert backfills == [True, False]
