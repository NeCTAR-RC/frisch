from __future__ import annotations

import importlib.resources

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from cliff.command import Command
from cliff.lister import Lister

from frisch import collect as collect_mod
from frisch.config import load_config
from frisch.db import queries
from frisch.db.session import make_session_factory


class _ConfiguredCommand(Command):
    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.add_argument("--config", help="path to a config file")
        return parser


class Collect(_ConfiguredCommand):
    """Run the collectors once (incremental)."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.add_argument("--source", action="append")
        parser.add_argument("--env", action="append")
        return parser

    def take_action(self, parsed_args):
        config = load_config(parsed_args.config)
        ok = collect_mod.run_collection(
            config, sources=parsed_args.source, envs=parsed_args.env
        )
        if not ok:
            raise SystemExit(1)


class Backfill(_ConfiguredCommand):
    """Re-walk all git history (idempotent)."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.add_argument(
            "--source", action="append", choices=["argocd", "puppet"]
        )
        parser.add_argument("--env", action="append")
        return parser

    def take_action(self, parsed_args):
        config = load_config(parsed_args.config)
        ok = collect_mod.run_collection(
            config,
            sources=parsed_args.source or ["argocd", "puppet"],
            envs=parsed_args.env,
            backfill=True,
        )
        if not ok:
            raise SystemExit(1)


class DbUpgrade(_ConfiguredCommand):
    """Create or upgrade the database schema (alembic upgrade head)."""

    def take_action(self, parsed_args):
        config = load_config(parsed_args.config)
        alembic_cfg = AlembicConfig()
        migrations = importlib.resources.files("frisch") / "migrations"
        alembic_cfg.set_main_option("script_location", str(migrations))
        alembic_cfg.set_main_option("sqlalchemy.url", config.database_url)
        alembic_command.upgrade(alembic_cfg, "head")


class ShowMatrix(_ConfiguredCommand, Lister):
    """The version matrix, in a terminal."""

    def take_action(self, parsed_args):
        config = load_config(parsed_args.config)
        session_factory = make_session_factory(config.database_url)
        with session_factory() as session:
            data = queries.matrix(session, config.environments)
        envs = data["environments"]
        columns = ["Service", "Source"]
        for env in envs:
            columns.extend([env, f"{env} changed"])

        def cell(row, env):
            info = row["environments"].get(env)
            if not info:
                return "-", ""
            if info.get("removed"):
                return "(removed)", info.get("changed_at") or ""
            if info.get("mixed"):
                return (
                    "mixed: " + ", ".join(info.get("versions") or []),
                    info.get("changed_at") or "",
                )
            version = info.get("version") or "-"
            if info.get("floating"):
                version += " (floating)"
            return version, info.get("changed_at") or ""

        rows = []
        for row in data["rows"]:
            values = [row["service"], row["source"]]
            for env in envs:
                version, changed = cell(row, env)
                values.extend([version, changed])
            rows.append(values)
        return columns, rows


class ShowStatus(_ConfiguredCommand, Lister):
    """Collector run status per source and environment."""

    def take_action(self, parsed_args):
        config = load_config(parsed_args.config)
        session_factory = make_session_factory(config.database_url)
        with session_factory() as session:
            data = queries.status(session)
        columns = [
            "Source",
            "Env",
            "Status",
            "Finished",
            "Last success",
            "Error",
        ]
        rows = [
            (
                s["source"],
                s["env"],
                s["status"],
                s["finished_at"] or "",
                s["last_success_at"] or "",
                (s["error"] or "")[:80],
            )
            for s in data["sources"]
        ]
        return columns, rows
