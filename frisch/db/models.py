"""SQLAlchemy models.

Portability rule: MariaDB in deployments, SQLite in dev/tests, so only
portable column types (strings, naive-UTC datetimes, JSON). ``component``
uses the empty string, not NULL, for the primary artefact so it can take
part in the instance unique constraint (NULLs never collide in SQL unique
constraints).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    pass


class Service(Base):
    __tablename__ = "service"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255))

    instances: Mapped[list[Instance]] = relationship(back_populates="service")


class Instance(Base):
    """One deployed artefact of a service in one environment, per source.

    ``current_*`` columns are denormalised from the latest event so the
    matrix query never scans history.
    """

    __tablename__ = "instance"
    __table_args__ = (
        UniqueConstraint(
            "source", "env", "source_key", "component", name="uq_instance"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("service.id"))
    source: Mapped[str] = mapped_column(String(32))
    env: Mapped[str] = mapped_column(String(32))
    source_key: Mapped[str] = mapped_column(String(255))
    component: Mapped[str] = mapped_column(String(64), default="")
    instance: Mapped[str | None] = mapped_column(String(64))
    current_version: Mapped[str | None] = mapped_column(String(255))
    current_version_kind: Mapped[str] = mapped_column(
        String(16), default="pinned"
    )
    current_changed_at: Mapped[datetime | None] = mapped_column(DateTime())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime())
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Denormalised from the latest observation, refreshed on every ingest
    # (unlike current_version, which only changes on a version change) so
    # e.g. the deb collector's mixed-across-nodes flag stays live. Nullable:
    # rows from before this column existed have no backfilled value until
    # next observed.
    current_meta: Mapped[dict | None] = mapped_column(JSON, default=dict)

    service: Mapped[Service] = relationship(back_populates="instances")
    events: Mapped[list[VersionEvent]] = relationship(
        back_populates="instance_rel"
    )


class VersionEvent(Base):
    """A recorded version change. The durable history; for deb packages the
    only history that exists anywhere."""

    __tablename__ = "version_event"
    __table_args__ = (
        UniqueConstraint("instance_id", "ref", name="uq_event_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instance.id"))
    version: Mapped[str | None] = mapped_column(String(255))
    previous_version: Mapped[str | None] = mapped_column(String(255))
    changed_at: Mapped[datetime] = mapped_column(DateTime())
    precision: Mapped[str] = mapped_column(String(16), default="exact")
    interval_start: Mapped[datetime | None] = mapped_column(DateTime())
    detected_at: Mapped[datetime] = mapped_column(DateTime())
    ref: Mapped[str] = mapped_column(String(128))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    instance_rel: Mapped[Instance] = relationship(back_populates="events")


class CollectorRun(Base):
    __tablename__ = "collector_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    env: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime())
    # running | success | unavailable | error
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class GitCursor(Base):
    """Last processed commit per mirrored repo walk."""

    __tablename__ = "git_cursor"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    sha: Mapped[str] = mapped_column(String(64))
