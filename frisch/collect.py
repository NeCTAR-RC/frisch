"""The ``frisch-collect`` entry point: run collectors and ingest.

One shot by default (the CronJob entry point); ``--interval N`` loops for
compose/dev. Exits non-zero when any source fails or is unavailable, so the
CronJob goes red instead of silently serving stale data. Run bookkeeping
uses its own transaction, so a failed run is still recorded; the ingested
events and cursor updates commit atomically per (source, env).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from sqlalchemy.orm import Session

from frisch.collectors.base import Collector
from frisch.collectors import build_collectors
from frisch.config import Config, load_config
from frisch.db.models import CollectorRun, GitCursor
from frisch.db.session import make_session_factory
from frisch.errors import FrischError, SourceUnavailable
from frisch.ingest import ingest, utcnow

LOG = logging.getLogger(__name__)


class DbCursorStore:
    """Cursors staged in the same session (and transaction) as the events."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, key: str) -> str | None:
        row = self.session.get(GitCursor, key)
        return row.sha if row else None

    def set(self, key: str, value: str) -> None:
        row = self.session.get(GitCursor, key)
        if row:
            row.sha = value
        else:
            self.session.add(GitCursor(key=key, sha=value))

    def delete(self, key: str) -> None:
        row = self.session.get(GitCursor, key)
        if row:
            self.session.delete(row)
            # Flush now: a backfill deletes the cursor and then re-adds the
            # same key in this session, and an unflushed DELETE would win
            # over the new row at commit, silently losing the cursor.
            self.session.flush()


def _run_one(
    session_factory, collector: Collector, env: str, backfill: bool
) -> bool:
    with session_factory() as bookkeeping:
        run = CollectorRun(
            source=collector.name,
            env=env,
            started_at=utcnow(),
            status="running",
        )
        bookkeeping.add(run)
        bookkeeping.commit()
        run_id = run.id

    status, error, stats = "success", None, {}
    try:
        with session_factory() as session:
            cursors = DbCursorStore(session)
            if backfill:
                result = collector.backfill(env, cursors)
            else:
                result = collector.collect(env, cursors)
            ingest_stats = ingest(
                session,
                collector.name,
                env,
                result.observations,
                full_snapshot=result.full_snapshot,
                snapshot_ref=result.snapshot_ref,
            )
            stats = {**result.stats, **ingest_stats.as_dict()}
            session.commit()
        LOG.info("%s/%s: %s", collector.name, env, stats)
    except SourceUnavailable as exc:
        status, error = "unavailable", str(exc)
        LOG.warning("%s/%s unavailable: %s", collector.name, env, exc)
    except (FrischError, Exception) as exc:
        status, error = "error", f"{type(exc).__name__}: {exc}"
        LOG.exception("%s/%s failed", collector.name, env)

    with session_factory() as bookkeeping:
        run = bookkeeping.get(CollectorRun, run_id)
        if run:
            run.finished_at = utcnow()
            run.status = status
            run.error = error
            run.stats = stats
            bookkeeping.commit()
    return status == "success"


def run_collection(
    config: Config,
    sources: list[str] | None = None,
    envs: list[str] | None = None,
    backfill: bool = False,
) -> bool:
    session_factory = make_session_factory(config.database_url)
    ok = True
    for collector in build_collectors(config, sources):
        for env in envs or config.environments:
            if env not in collector.environments():
                continue
            if not _run_one(session_factory, collector, env, backfill):
                ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="frisch-collect",
        description="Collect deployed versions from the configured sources.",
    )
    parser.add_argument("--config", help="path to a config file")
    parser.add_argument(
        "--source",
        action="append",
        choices=["argocd", "puppet", "deb"],
        help="only these sources (repeatable; default all)",
    )
    parser.add_argument(
        "--env",
        action="append",
        help="only these environments (repeatable; default all configured)",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="re-walk all git history (git sources only)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        metavar="SECONDS",
        help="loop forever with this many seconds between runs",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    while True:
        ok = run_collection(
            config,
            sources=args.source,
            envs=args.env,
            backfill=args.backfill,
        )
        if args.interval is None:
            return 0 if ok else 1
        args.backfill = False  # only ever backfill on the first loop
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
