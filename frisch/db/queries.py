"""Read-side queries: the matrix, per-service detail/history, status.

Matrix rows are per (service, source): a chart version and an image tag are
different version schemes for the same logical service, so they are shown as
separate rows rather than blended. Within a row, an environment aggregates
its active primary instances -- one version when they all agree, otherwise a
``mixed`` flag with the per-instance breakdown on the detail page.
Promotion lag is inequality plus both dates; version strings are never
ordered.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from frisch.db.models import CollectorRun, Instance, Service, VersionEvent
from frisch import model


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _aggregate_env(instances: list[Instance]) -> dict | None:
    active = [i for i in instances if i.active]
    if not instances:
        return None
    if not active:
        return {
            "version": None,
            "removed": True,
            "changed_at": _iso(
                max(
                    (
                        i.current_changed_at
                        for i in instances
                        if i.current_changed_at
                    ),
                    default=None,
                )
            ),
            "mixed": False,
            "floating": False,
            "instances": 0,
        }
    versions = {i.current_version for i in active}
    floating = any(i.current_version_kind == model.FLOATING for i in active)
    changed = max(
        (i.current_changed_at for i in active if i.current_changed_at),
        default=None,
    )
    return {
        "version": active[0].current_version if len(versions) == 1 else None,
        "versions": sorted(v for v in versions if v)
        if len(versions) > 1
        else None,
        "mixed": len(versions) > 1,
        "floating": floating,
        "changed_at": _iso(changed),
        "instances": len(active),
        "removed": False,
    }


def matrix(
    session: Session,
    environments: list[str],
    instance: str | None = None,
) -> dict:
    services = (
        session.scalars(
            select(Service)
            .options(joinedload(Service.instances))
            .order_by(Service.name)
        )
        .unique()
        .all()
    )
    # Every fan-out/cluster instance name with a live deployment, so the UI
    # can offer the filter choices even while one is selected.
    instances = sorted(
        session.scalars(
            select(Instance.instance)
            .where(
                Instance.component == model.PRIMARY,
                Instance.instance.is_not(None),
                Instance.active,
            )
            .distinct()
        ).all()
    )
    rows = []
    for service in services:
        primaries = [
            i
            for i in service.instances
            if i.component == model.PRIMARY
            and (instance is None or i.instance == instance)
        ]
        for source in sorted({i.source for i in primaries}):
            by_env = {}
            for env in environments:
                agg = _aggregate_env(
                    [
                        i
                        for i in primaries
                        if i.source == source and i.env == env
                    ]
                )
                if agg is not None:
                    by_env[env] = agg
            if not by_env:
                continue
            # A row that is removed (or was never present) in every
            # environment is history, not current state: keep it out of the
            # matrix. It stays reachable via the service detail/history.
            if all(e["removed"] for e in by_env.values()):
                continue
            headline = {
                env: e["version"]
                for env, e in by_env.items()
                if not e["removed"]
            }
            distinct = {v for v in headline.values()}
            differs = len(headline) > 1 and (
                len(distinct) > 1
                or any(e.get("mixed") for e in by_env.values())
            )
            rows.append(
                {
                    "service": service.name,
                    "display_name": service.display_name,
                    "source": source,
                    "environments": by_env,
                    "differs": differs,
                }
            )
    return {
        "environments": environments,
        "instances": instances,
        "rows": rows,
    }


def _instance_dict(inst: Instance) -> dict:
    meta = inst.current_meta or {}
    return {
        "id": inst.id,
        "source": inst.source,
        "env": inst.env,
        "source_key": inst.source_key,
        "component": inst.component,
        "instance": inst.instance,
        "version": inst.current_version,
        "version_kind": inst.current_version_kind,
        "changed_at": _iso(inst.current_changed_at),
        "last_seen_at": _iso(inst.last_seen_at),
        "active": inst.active,
        "mixed": bool(meta.get("mixed")),
        "versions": meta.get("versions") if meta.get("mixed") else None,
    }


def _event_dict(event: VersionEvent, inst: Instance) -> dict:
    return {
        "env": inst.env,
        "source": inst.source,
        "source_key": inst.source_key,
        "component": inst.component,
        "instance": inst.instance,
        "version": event.version,
        "previous_version": event.previous_version,
        "changed_at": _iso(event.changed_at),
        "precision": event.precision,
        "interval_start": _iso(event.interval_start),
        "meta": event.meta,
    }


def service_detail(session: Session, name: str) -> dict | None:
    service = session.scalar(
        select(Service)
        .options(joinedload(Service.instances))
        .where(Service.name == name)
    )
    if service is None:
        return None
    return {
        "service": service.name,
        "display_name": service.display_name,
        "instances": sorted(
            (_instance_dict(i) for i in service.instances),
            key=lambda d: (
                d["source"],
                d["env"],
                d["source_key"],
                d["component"],
            ),
        ),
    }


def service_history(
    session: Session,
    name: str,
    env: str | None = None,
    limit: int = 200,
) -> list[dict]:
    query = (
        select(VersionEvent, Instance)
        .join(Instance, VersionEvent.instance_id == Instance.id)
        .join(Service, Instance.service_id == Service.id)
        .where(Service.name == name)
        .order_by(VersionEvent.changed_at.desc(), VersionEvent.id.desc())
        .limit(limit)
    )
    if env:
        query = query.where(Instance.env == env)
    return [
        _event_dict(event, inst)
        for event, inst in session.execute(query).all()
    ]


def status(session: Session) -> dict:
    runs = session.scalars(
        select(CollectorRun)
        .order_by(CollectorRun.started_at.desc())
        .limit(200)
    ).all()
    latest: dict[tuple[str, str], CollectorRun] = {}
    latest_success: dict[tuple[str, str], CollectorRun] = {}
    for run in runs:
        key = (run.source, run.env)
        latest.setdefault(key, run)
        if run.status == "success":
            latest_success.setdefault(key, run)
    return {
        "sources": [
            {
                "source": source,
                "env": env,
                "status": run.status,
                "started_at": _iso(run.started_at),
                "finished_at": _iso(run.finished_at),
                "error": run.error,
                "stats": run.stats,
                "last_success_at": _iso(
                    latest_success[(source, env)].finished_at
                )
                if (source, env) in latest_success
                else None,
            }
            for (source, env), run in sorted(latest.items())
        ]
    }
