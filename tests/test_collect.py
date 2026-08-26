"""Integration tests over run_collection: cursor persistence across runs,
run bookkeeping, and the non-zero-exit contract."""

from sqlalchemy import create_engine, select

from frisch.config import ArgocdConfig, Config, GitRepoConfig
from frisch.collect import run_collection
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
