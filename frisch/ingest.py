"""The single writer: turns collector observations into stored state.

Observations arrive oldest-first. For each one we replay it against the
instance's running state: the current_* columns always track the observation
being processed, while a :class:`VersionEvent` is appended only when the
version actually changes and no event with the same ``(instance, ref)``
exists yet. That makes full re-walks of git history (backfill re-runs,
cursor resets after a force-push) idempotent: every event already exists, and
the replay converges on the same current state.

Floating versions (``targetRevision: HEAD``, image tag ``latest``) update
current state but never produce events -- there is nothing meaningful to
timestamp.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, UTC

from sqlalchemy.orm import Session
from sqlalchemy import select

from frisch.db.models import Instance, Service, VersionEvent
from frisch import model

LOG = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclasses.dataclass
class IngestStats:
    observations: int = 0
    events: int = 0
    tombstones: int = 0
    instances_created: int = 0

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


class Ingester:
    def __init__(self, session: Session):
        self.session = session
        self._services: dict[str, Service] = {}
        self._instances: dict[tuple[str, str, str, str], Instance] = {}

    def _service(self, name: str) -> Service:
        svc = self._services.get(name)
        if svc is None:
            svc = self.session.scalar(
                select(Service).where(Service.name == name)
            )
        if svc is None:
            svc = Service(name=name)
            self.session.add(svc)
            self.session.flush()
        self._services[name] = svc
        return svc

    def _instance(
        self, obs: model.Observation, stats: IngestStats
    ) -> Instance:
        key = (obs.source, obs.env, obs.source_key, obs.component)
        inst = self._instances.get(key)
        if inst is None:
            inst = self.session.scalar(
                select(Instance).where(
                    Instance.source == obs.source,
                    Instance.env == obs.env,
                    Instance.source_key == obs.source_key,
                    Instance.component == obs.component,
                )
            )
        if inst is None:
            inst = Instance(
                service_id=self._service(obs.service).id,
                source=obs.source,
                env=obs.env,
                source_key=obs.source_key,
                component=obs.component,
                instance=obs.instance,
                active=False,  # becomes True when a live version lands
            )
            self.session.add(inst)
            self.session.flush()
            stats.instances_created += 1
        self._instances[key] = inst
        return inst

    def _event_exists(self, inst: Instance, ref: str) -> bool:
        return (
            self.session.scalar(
                select(VersionEvent.id).where(
                    VersionEvent.instance_id == inst.id,
                    VersionEvent.ref == ref,
                )
            )
            is not None
        )

    def _append_event(
        self, inst: Instance, obs: model.Observation, prev: str | None
    ) -> bool:
        if self._event_exists(inst, obs.ref):
            return False
        interval_start = obs.interval_start
        if (
            interval_start is None
            and obs.precision == model.INTERVAL
            and inst.last_seen_at is not None
        ):
            interval_start = inst.last_seen_at
        self.session.add(
            VersionEvent(
                instance_id=inst.id,
                version=obs.version,
                previous_version=prev,
                changed_at=obs.changed_at,
                precision=obs.precision,
                interval_start=interval_start,
                detected_at=utcnow(),
                ref=obs.ref,
                meta=obs.meta,
            )
        )
        return True

    def ingest_one(self, obs: model.Observation, stats: IngestStats) -> None:
        stats.observations += 1
        inst = self._instance(obs, stats)

        # A service mapping can change (config alias added); follow it.
        svc = self._service(obs.service)
        if inst.service_id != svc.id:
            inst.service_id = svc.id
        if obs.instance is not None and inst.instance != obs.instance:
            inst.instance = obs.instance

        if obs.version is None:  # tombstone: removed from the source
            if inst.active:
                if self._append_event(inst, obs, inst.current_version):
                    stats.events += 1
                stats.tombstones += 1
                inst.active = False
                inst.current_version = None
                inst.current_changed_at = obs.changed_at
            return

        if obs.version_kind == model.FLOATING:
            # Floating never produces events; just track state.
            inst.current_version = obs.version
            inst.current_version_kind = model.FLOATING
            inst.current_changed_at = obs.changed_at
        elif obs.version != inst.current_version:
            if self._append_event(inst, obs, inst.current_version):
                stats.events += 1
            inst.current_version = obs.version
            inst.current_version_kind = obs.version_kind
            inst.current_changed_at = obs.changed_at
        inst.active = True
        if inst.last_seen_at is None or obs.changed_at > inst.last_seen_at:
            inst.last_seen_at = obs.changed_at

    def tombstone_unseen(
        self,
        source: str,
        env: str,
        seen: set[tuple[str, str]],
        snapshot_ref: str,
        when: datetime,
        stats: IngestStats,
    ) -> None:
        """After a full snapshot (deb collector), anything active that the
        snapshot did not report has disappeared from the source."""
        active = self.session.scalars(
            select(Instance).where(
                Instance.source == source,
                Instance.env == env,
                Instance.active == True,  # noqa: E712
            )
        )
        for inst in active:
            if (inst.source_key, inst.component) in seen:
                continue
            obs = model.Observation(
                source=source,
                env=env,
                source_key=inst.source_key,
                service=inst.service.name,
                version=None,
                changed_at=when,
                ref=snapshot_ref,
                component=inst.component,
                precision=model.INTERVAL,
            )
            self.ingest_one(obs, stats)


def ingest(
    session: Session,
    source: str,
    env: str,
    observations: list[model.Observation],
    full_snapshot: bool = False,
    snapshot_ref: str | None = None,
) -> IngestStats:
    stats = IngestStats()
    ing = Ingester(session)
    seen: set[tuple[str, str]] = set()
    when = utcnow()
    for obs in observations:
        if obs.source != source or obs.env != env:
            raise ValueError(
                f"observation for {obs.source}/{obs.env} in {source}/{env} run"
            )
        ing.ingest_one(obs, stats)
        seen.add((obs.source_key, obs.component))
    if full_snapshot:
        ing.tombstone_unseen(
            source,
            env,
            seen,
            snapshot_ref or f"gone:{when.isoformat()}",
            when,
            stats,
        )
    return stats
