"""The JSON API under /api/v1. Read-only; all writes go through ingest."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from frisch import __version__
from frisch.db import queries


def _session(request: Request):
    return request.app.state.session_factory()


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/matrix")
    def matrix(request: Request):
        with _session(request) as session:
            return queries.matrix(
                session, request.app.state.config.environments
            )

    @router.get("/services")
    def services(request: Request):
        with _session(request) as session:
            data = queries.matrix(
                session, request.app.state.config.environments
            )
        return {"services": sorted({row["service"] for row in data["rows"]})}

    @router.get("/services/{name}")
    def service(request: Request, name: str):
        with _session(request) as session:
            detail = queries.service_detail(session, name)
        if detail is None:
            raise HTTPException(status_code=404, detail="unknown service")
        return detail

    @router.get("/services/{name}/history")
    def history(
        request: Request,
        name: str,
        env: str | None = None,
        limit: int = Query(default=200, le=1000),
    ):
        with _session(request) as session:
            if queries.service_detail(session, name) is None:
                raise HTTPException(status_code=404, detail="unknown service")
            return {
                "service": name,
                "events": queries.service_history(
                    session, name, env=env, limit=limit
                ),
            }

    @router.get("/status")
    def status(request: Request):
        with _session(request) as session:
            data = queries.status(session)
        data["version"] = __version__
        return data

    return router
