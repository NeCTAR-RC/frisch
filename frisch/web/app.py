"""FastAPI app factory and the ``frisch-web`` console entry point.

Serves the JSON API under ``/api``, ``/healthz``, prometheus ``/metrics``,
and -- when a built SPA bundle is present (FRISCH_WEB_STATIC) -- the SPA for
every other path, with a client-side-routing fallback to ``index.html``.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from prometheus_client import (
    CollectorRegistry,
    CONTENT_TYPE_LATEST,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select

from frisch import __version__
from frisch.config import Config, load_config
from frisch.db.models import CollectorRun, VersionEvent
from frisch.db.session import make_session_factory
from frisch.web.api import build_router


class _DbMetrics:
    """Collector-freshness and event-count gauges, read at scrape time."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def collect(self):
        last_success = GaugeMetricFamily(
            "frisch_collector_last_success_timestamp",
            "Unix time of the last successful run per source/env",
            labels=["source", "env"],
        )
        events = GaugeMetricFamily(
            "frisch_version_events_total",
            "Recorded version change events",
        )
        with self.session_factory() as session:
            rows = session.execute(
                select(
                    CollectorRun.source,
                    CollectorRun.env,
                    func.max(CollectorRun.finished_at),
                )
                .where(CollectorRun.status == "success")
                .group_by(CollectorRun.source, CollectorRun.env)
            ).all()
            for source, env, finished in rows:
                if finished is not None:
                    last_success.add_metric(
                        [source, env], finished.timestamp()
                    )
            count = session.scalar(select(func.count(VersionEvent.id)))
            events.add_metric([], count or 0)
        yield last_success
        yield events


def create_app(config: Config | None = None) -> FastAPI:
    if config is None:
        config = load_config()
    app = FastAPI(
        title="frisch",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.config = config
    app.state.session_factory = make_session_factory(config.database_url)
    app.include_router(build_router())

    registry = CollectorRegistry()
    registry.register(_DbMetrics(app.state.session_factory))

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(
            generate_latest(registry), media_type=CONTENT_TYPE_LATEST
        )

    _mount_spa(app, config)
    return app


def _mount_spa(app: FastAPI, config: Config) -> None:
    static_dir = config.web_static
    if static_dir is None:
        return
    static_dir = static_dir.resolve()
    index = static_dir / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # The API router is registered first and wins; anything reaching
        # here that still looks like an API path is a genuine miss.
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = (static_dir / full_path).resolve()
        if static_dir in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        if index.is_file():
            # client-side route -> serve the SPA shell
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="not found")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    import uvicorn

    from frisch.config import ENV_PREFIX

    parser = argparse.ArgumentParser(
        prog="frisch-web",
        description="Serve the frisch dashboard (read-only).",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", help="path to a config file")
    parser.add_argument("--static-dir", help="SPA bundle to serve")
    parser.add_argument(
        "--reload", action="store_true", help="auto-reload (development)"
    )
    args = parser.parse_args(argv)

    # The factory (possibly in a reloader subprocess) reads settings from
    # the environment.
    if args.config:
        os.environ[ENV_PREFIX + "CONFIG"] = args.config
    if args.static_dir:
        os.environ[ENV_PREFIX + "WEB_STATIC"] = args.static_dir

    uvicorn.run(
        "frisch.web.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
