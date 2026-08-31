"""The cliff CLI: argument wiring, exit codes and the terminal tables."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect

from frisch.cli import commands
from frisch.cli.main import main
from frisch.db.models import Base, CollectorRun
from frisch.db.session import make_session_factory
from frisch.ingest import ingest
from frisch import model


@pytest.fixture
def config_file(tmp_path):
    """A config file pointing at an empty, migrated sqlite database."""
    db = tmp_path / "frisch.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{db}"))
    path = tmp_path / "frisch.yaml"
    path.write_text(
        f"database:\n  url: sqlite:///{db}\nenvironments: [test, prod]\n"
    )
    return path


def observation(env, service, version, ref, **kwargs):
    return model.Observation(
        source="argocd",
        env=env,
        source_key=kwargs.pop("source_key", f"main/{service}"),
        service=service,
        version=version,
        changed_at=datetime(2026, 2, 1),
        ref=ref,
        **kwargs,
    )


def db_session(config_file):
    url = f"sqlite:///{config_file.parent}/frisch.db"
    return make_session_factory(url)()


def add_run(config_file, **kwargs):
    with db_session(config_file) as session:
        session.add(
            CollectorRun(
                source="argocd",
                env="test",
                started_at=datetime(2026, 2, 1),
                finished_at=datetime(2026, 2, 1),
                **kwargs,
            )
        )
        session.commit()


def run_args(command, argv):
    """Parse ``argv`` the way cliff would and take the action."""
    cmd = command(None, None)
    parsed = cmd.get_parser(command.__name__).parse_args(argv)
    return cmd.take_action(parsed)


def test_collect_passes_filters_through(config_file, monkeypatch):
    seen = {}

    def fake_run(config, sources=None, envs=None, backfill=False):
        seen.update(
            database_url=config.database_url,
            sources=sources,
            envs=envs,
            backfill=backfill,
        )
        return True

    monkeypatch.setattr(commands.collect_mod, "run_collection", fake_run)
    run_args(
        commands.Collect,
        ["--config", str(config_file), "--source", "argocd", "--env", "test"],
    )

    assert seen["sources"] == ["argocd"]
    assert seen["envs"] == ["test"]
    assert seen["backfill"] is False
    assert seen["database_url"].startswith("sqlite:///")


def test_collect_exits_non_zero_on_failure(config_file, monkeypatch):
    monkeypatch.setattr(
        commands.collect_mod, "run_collection", lambda *a, **k: False
    )
    with pytest.raises(SystemExit) as exc:
        run_args(commands.Collect, ["--config", str(config_file)])
    assert exc.value.code == 1


def test_backfill_defaults_to_the_git_sources(config_file, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        commands.collect_mod,
        "run_collection",
        lambda config, **kwargs: (seen.update(kwargs), True)[1],
    )
    run_args(commands.Backfill, ["--config", str(config_file)])

    assert seen["sources"] == ["argocd", "puppet"]
    assert seen["backfill"] is True


def test_backfill_exits_non_zero_on_failure(config_file, monkeypatch):
    monkeypatch.setattr(
        commands.collect_mod, "run_collection", lambda *a, **k: False
    )
    with pytest.raises(SystemExit) as exc:
        run_args(
            commands.Backfill,
            ["--config", str(config_file), "--source", "puppet"],
        )
    assert exc.value.code == 1


def test_db_upgrade_creates_the_schema(tmp_path):
    db = tmp_path / "fresh.db"
    path = tmp_path / "frisch.yaml"
    path.write_text(f"database:\n  url: sqlite:///{db}\n")

    run_args(commands.DbUpgrade, ["--config", str(path)])

    tables = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert {"version_event", "collector_run", "git_cursor"} <= tables


def test_show_matrix_renders_every_cell_shape(config_file):
    with db_session(config_file) as session:
        ingest(
            session,
            "argocd",
            "test",
            [
                observation("test", "keystone", "1.1.0", "t1"),
                observation(
                    "test",
                    "nova",
                    "HEAD",
                    "t2",
                    version_kind=model.FLOATING,
                ),
                observation(
                    "test",
                    "glance",
                    "2.0.0",
                    "t3",
                    source_key="main/glance",
                    instance="qld",
                ),
                observation(
                    "test",
                    "glance",
                    "2.1.0",
                    "t4",
                    source_key="main/glance-vic",
                    instance="vic",
                ),
                observation("test", "cinder", "9.9.9", "t5"),
            ],
        )
        ingest(
            session,
            "argocd",
            "prod",
            [
                observation("prod", "keystone", "1.0.0", "p1"),
                observation("prod", "cinder", "9.9.9", "p2"),
            ],
        )
        # A later prod run without cinder retires it.
        ingest(
            session,
            "argocd",
            "prod",
            [observation("prod", "keystone", "1.0.0", "p1")],
            full_snapshot=True,
            snapshot_ref="p3",
        )
        session.commit()

    columns, rows = run_args(
        commands.ShowMatrix, ["--config", str(config_file)]
    )

    assert columns == [
        "Service",
        "Source",
        "test",
        "test changed",
        "prod",
        "prod changed",
    ]
    by_service = {row[0]: row for row in rows}

    assert by_service["keystone"][2] == "1.1.0"
    assert by_service["keystone"][3]
    assert by_service["keystone"][4] == "1.0.0"
    # Only deployed in test: the prod cell is empty, not a fake version.
    assert by_service["nova"][2] == "HEAD (floating)"
    assert by_service["nova"][4] == "-"
    # Two instances on different versions.
    assert by_service["glance"][2].startswith("mixed: ")
    assert "2.0.0" in by_service["glance"][2]
    assert "2.1.0" in by_service["glance"][2]
    # Still in test, retired from prod.
    assert by_service["cinder"][2] == "9.9.9"
    assert by_service["cinder"][4] == "(removed)"


def test_show_status_lists_runs(config_file, monkeypatch):
    monkeypatch.setattr(
        commands.collect_mod, "run_collection", lambda *a, **k: True
    )
    # No collector has run yet: the table is empty but well-formed.
    columns, rows = run_args(
        commands.ShowStatus, ["--config", str(config_file)]
    )
    assert columns[0] == "Source"
    assert rows == []


def test_show_status_truncates_long_errors(config_file):
    add_run(config_file, status="error", error="x" * 200)

    _, rows = run_args(commands.ShowStatus, ["--config", str(config_file)])
    (row,) = rows
    assert row[0] == "argocd"
    assert row[2] == "error"
    assert len(row[5]) == 80


def test_main_dispatches_to_a_command(config_file, capsys):
    add_run(config_file, status="success")
    assert main(["show", "status", "--config", str(config_file)]) == 0
    out = capsys.readouterr().out
    assert "Source" in out
    assert "argocd" in out
