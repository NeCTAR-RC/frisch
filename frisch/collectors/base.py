"""The collector contract.

Collectors are pure producers: :meth:`Collector.collect` returns observations
plus updated cursors and never touches the database. Cursor persistence goes
through the :class:`CursorStore` the runner passes in, so cursor updates
commit atomically with the ingested events.
"""

from __future__ import annotations

import abc
import dataclasses
from typing import Protocol

from frisch.config import Config
from frisch.errors import BackfillUnsupported
from frisch.model import Observation, ServiceIdentity


class CursorStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


@dataclasses.dataclass
class CollectResult:
    observations: list[Observation]
    # True when the observation list is the complete current state of the
    # source (deb snapshots): anything active but unreported is gone.
    full_snapshot: bool = False
    snapshot_ref: str | None = None
    stats: dict = dataclasses.field(default_factory=dict)


class Collector(abc.ABC):
    name: str

    def __init__(self, config: Config, identity: ServiceIdentity):
        self.config = config
        self.identity = identity

    @abc.abstractmethod
    def environments(self) -> list[str]:
        """Environments this collector is configured for."""

    @abc.abstractmethod
    def collect(self, env: str, cursors: CursorStore) -> CollectResult:
        """Incremental collection; a missing cursor means full history."""

    def cursor_keys(self, env: str) -> list[str]:
        return []

    def backfill(self, env: str, cursors: CursorStore) -> CollectResult:
        """Re-walk all available history (git sources only)."""
        keys = self.cursor_keys(env)
        if not keys:
            raise BackfillUnsupported(
                f"source '{self.name}' has no history to backfill"
            )
        for key in keys:
            cursors.delete(key)
        return self.collect(env, cursors)
